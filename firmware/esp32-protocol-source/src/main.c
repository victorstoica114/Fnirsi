/* Classic ESP32: high-level ESP-IDF drivers, continuously active test sources. */
#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sdkconfig.h"
#include "esp_err.h"
#include "esp_timer.h"
#include "esp_rom_sys.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "driver/uart.h"
#include "driver/spi_master.h"
#include "driver/i2c_master.h"
#include "driver/i2c_slave.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "frame.h"

#if !CONFIG_IDF_TARGET_ESP32 || !CONFIG_I2C_ENABLE_SLAVE_DRIVER_VERSION_2
#error "This generator requires classic ESP32 and the I2C slave v2 driver."
#endif
#if defined(CONFIG_PM_ENABLE) || defined(CONFIG_FREERTOS_USE_TICKLESS_IDLE)
#error "Keep APB frequency fixed and automatic light sleep disabled."
#endif

enum { PWM_GPIO=32, UART_GPIO=25, SDA_GPIO=21, SCL_GPIO=22,
       SLAVE_SDA_GPIO=26, SLAVE_SCL_GPIO=33, SPI_CLK_GPIO=18,
       SPI_MOSI_GPIO=23, SPI_MISO_GPIO=19, SPI_CS_GPIO=27, SLAVE_ADDR=0x42 };
typedef struct { uint32_t length; uint8_t bytes[FRAME_SIZE]; } received_t;
typedef struct { char kind; uint32_t value; } command_t;
static QueueHandle_t received_queue, command_queue;
static spi_device_handle_t spi_device;
static i2c_slave_dev_handle_t slave_device;
static i2c_master_dev_handle_t master_device;
static i2c_master_bus_handle_t master_bus;
static uint32_t sequence, uart_baud=115200, spi_hz=1000000, i2c_hz=100000;
static uint32_t gap_ms=5, uart_ok, uart_bad, spi_ok, spi_bad, i2c_write_ok, i2c_write_bad;
static uint32_t i2c_read_ok, i2c_read_bad;
static volatile uint32_t callback_drops;
static esp_err_t last_spi_error, last_i2c_write_error, last_i2c_read_error;
static uint16_t last_spi_tx_crc, last_spi_rx_crc;
static uint32_t last_i2c_received, last_i2c_prefilled;
/* Current wiring has no verified receiver path. Use an explicit write/NACK
 * test pattern by default; full ACK/read/write checks remain opt-in. */
static bool i2c_write_only=true, slave_swapped;
static volatile bool topology_active;
static uint32_t i2c_emitted_ok, i2c_emitted_bad;

/* Passive level correlation. Weak pull-ups bound floating receiver inputs;
 * Original pulls are restored after this four-second diagnostic. Input gates
 * stay enabled for monitoring; output routing and PWM are never modified. */
static void topology_task(void *arg)
{
    (void)arg;
    const gpio_num_t sources[3]={SDA_GPIO,SCL_GPIO,SPI_MOSI_GPIO};
    const gpio_num_t receivers[3]={SPI_MISO_GPIO,SLAVE_SDA_GPIO,SLAVE_SCL_GPIO};
    gpio_io_config_t original[6];
    uint32_t joint[3][3][4]={0}, total=0;
    for (unsigned i=0;i<6;i++) {
        gpio_num_t pin=i<3?sources[i]:receivers[i-3];
        ESP_ERROR_CHECK(gpio_get_io_config(pin,&original[i]));
        ESP_ERROR_CHECK(gpio_input_enable(pin));
        if (i>=3) ESP_ERROR_CHECK(gpio_set_pull_mode(pin,GPIO_PULLUP_ONLY));
    }
    printf("TOPOLOGY_BEGIN input_only=1 weak_pullups=1 seconds=4\n");
    int64_t end=esp_timer_get_time()+4000000;
    while (esp_timer_get_time()<end) {
        for (unsigned sample=0;sample<100;sample++) {
            int before[3],received[3],after[3];
            for (unsigned s=0;s<3;s++) before[s]=gpio_get_level(sources[s]);
            for (unsigned r=0;r<3;r++) received[r]=gpio_get_level(receivers[r]);
            for (unsigned s=0;s<3;s++) after[s]=gpio_get_level(sources[s]);
            for (unsigned r=0;r<3;r++) for (unsigned s=0;s<3;s++)
                if (before[s]==after[s]) joint[r][s][2*before[s]+received[r]]++;
            total++;
            esp_rom_delay_us(4);
        }
        vTaskDelay(1);
    }
    for (unsigned i=0;i<6;i++) {
        gpio_num_t pin=i<3?sources[i]:receivers[i-3];
        if (i>=3) {
            gpio_pull_mode_t pull=original[i].pu?
                (original[i].pd?GPIO_PULLUP_PULLDOWN:GPIO_PULLUP_ONLY):
                (original[i].pd?GPIO_PULLDOWN_ONLY:GPIO_FLOATING);
            ESP_ERROR_CHECK(gpio_set_pull_mode(pin,pull));
        }
    }
    for (unsigned r=0;r<3;r++) for (unsigned s=0;s<3;s++)
        printf("TOPOLOGY_PAIR receiver=%d source=%d n00=%" PRIu32
               " n01=%" PRIu32 " n10=%" PRIu32 " n11=%" PRIu32 "\n",
               (int)receivers[r],(int)sources[s],joint[r][s][0],joint[r][s][1],
               joint[r][s][2],joint[r][s][3]);
    printf("TOPOLOGY_END samples=%" PRIu32 " pulls_restored=1\n",total);
    fflush(stdout);
    topology_active=false;
    vTaskDelete(NULL);
}

static bool slave_received(i2c_slave_dev_handle_t device,
                           const i2c_slave_rx_done_event_data_t *event, void *arg)
{
    (void)device; (void)arg;
    received_t item = { .length=event->length };
    if (event->length <= FRAME_SIZE)
        memcpy(item.bytes, event->buffer, event->length);
    BaseType_t awakened = pdFALSE;
    if (xQueueSendFromISR(received_queue, &item, &awakened) != pdTRUE)
        callback_drops++;
    return awakened == pdTRUE;
}

static void configure_pwm(void)
{
    ledc_timer_config_t timer = {
        .speed_mode=LEDC_HIGH_SPEED_MODE, .timer_num=LEDC_TIMER_0,
        .duty_resolution=LEDC_TIMER_1_BIT, .freq_hz=1000000,
        .clk_cfg=LEDC_USE_APB_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&timer));
    ledc_channel_config_t channel = {
        .gpio_num=PWM_GPIO, .speed_mode=LEDC_HIGH_SPEED_MODE,
        .channel=LEDC_CHANNEL_0, .intr_type=LEDC_INTR_DISABLE,
        .timer_sel=LEDC_TIMER_0, .duty=1, .hpoint=0,
    };
    ESP_ERROR_CHECK(ledc_channel_config(&channel));
}

static void configure_uart(void)
{
    uart_config_t config = { .baud_rate=(int)uart_baud,
        .data_bits=UART_DATA_8_BITS, .parity=UART_PARITY_DISABLE,
        .stop_bits=UART_STOP_BITS_1, .flow_ctrl=UART_HW_FLOWCTRL_DISABLE,
        .source_clk=UART_SCLK_APB };
    ESP_ERROR_CHECK(uart_param_config(UART_NUM_1, &config));
    ESP_ERROR_CHECK(uart_set_pin(UART_NUM_1, UART_GPIO, UART_PIN_NO_CHANGE,
                                 UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
    ESP_ERROR_CHECK(uart_driver_install(UART_NUM_1, 256, 0, 0, NULL, 0));
    /* UART0 is the existing CH340 console, not an analyzer test signal. */
    ESP_ERROR_CHECK(uart_driver_install(UART_NUM_0, 1024, 0, 0, NULL, 0));
}

static esp_err_t add_spi_device(uint32_t hz)
{
    spi_device_interface_config_t device = {
        .clock_speed_hz=(int)hz, .mode=0, .spics_io_num=SPI_CS_GPIO,
        .queue_size=1, .cs_ena_pretrans=1, .cs_ena_posttrans=1,
    };
    return spi_bus_add_device(SPI3_HOST, &device, &spi_device);
}

static void configure_spi(void)
{
    spi_bus_config_t bus = { .mosi_io_num=SPI_MOSI_GPIO,
        .miso_io_num=SPI_MISO_GPIO, .sclk_io_num=SPI_CLK_GPIO,
        .quadwp_io_num=-1, .quadhd_io_num=-1, .max_transfer_sz=FRAME_SIZE };
    ESP_ERROR_CHECK(spi_bus_initialize(SPI3_HOST, &bus, SPI_DMA_CH_AUTO));
    ESP_ERROR_CHECK(add_spi_device(spi_hz));
}

static esp_err_t add_master_device(uint32_t hz)
{
    i2c_device_config_t config = { .dev_addr_length=I2C_ADDR_BIT_LEN_7,
        .device_address=SLAVE_ADDR, .scl_speed_hz=hz,
        .flags.disable_ack_check=i2c_write_only };
    return i2c_master_bus_add_device(master_bus, &config, &master_device);
}

static esp_err_t add_slave_device(bool swapped)
{
    i2c_slave_config_t slave = { .i2c_port=1,
        .sda_io_num=swapped?SLAVE_SCL_GPIO:SLAVE_SDA_GPIO,
        .scl_io_num=swapped?SLAVE_SDA_GPIO:SLAVE_SCL_GPIO,
        .clk_source=I2C_CLK_SRC_APB, .slave_addr=SLAVE_ADDR,
        .addr_bit_len=I2C_ADDR_BIT_LEN_7, .send_buf_depth=64,
        .receive_buf_depth=64, .flags.enable_internal_pullup=false };
    esp_err_t error=i2c_new_slave_device(&slave, &slave_device);
    if (error!=ESP_OK) return error;
    i2c_slave_event_callbacks_t callbacks = { .on_receive=slave_received };
    error=i2c_slave_register_event_callbacks(slave_device,&callbacks,NULL);
    if (error!=ESP_OK) ESP_ERROR_CHECK(i2c_del_slave_device(slave_device));
    return error;
}

static void configure_i2c(void)
{
    received_queue = xQueueCreate(4, sizeof(received_t));
    configASSERT(received_queue);
    ESP_ERROR_CHECK(add_slave_device(slave_swapped));
    i2c_master_bus_config_t master = { .i2c_port=0,
        .sda_io_num=SDA_GPIO, .scl_io_num=SCL_GPIO,
        .clk_source=I2C_CLK_SRC_APB, .glitch_ignore_cnt=7,
        .flags.enable_internal_pullup=false };
    ESP_ERROR_CHECK(i2c_new_master_bus(&master, &master_bus));
    ESP_ERROR_CHECK(add_master_device(i2c_hz));
}

static void print_status(void)
{
    printf("DLA_SOURCE v2 seq=%" PRIu32 " pwm=%" PRIu32 " duty=1/2"
           " uart=%" PRIu32 " spi=%" PRIu32 " i2c=%" PRIu32 " gap=%" PRIu32
           " uart_ok=%" PRIu32 " uart_bad=%" PRIu32 " spi_ok=%" PRIu32 " spi_bad=%" PRIu32
           " i2c_write_ok=%" PRIu32 " i2c_write_bad=%" PRIu32
           " i2c_read_ok=%" PRIu32 " i2c_read_bad=%" PRIu32
           " callback_drops=%" PRIu32 "\n",
           sequence, ledc_get_freq(LEDC_HIGH_SPEED_MODE, LEDC_TIMER_0),
           uart_baud, spi_hz, i2c_hz, gap_ms, uart_ok, uart_bad, spi_ok, spi_bad,
           i2c_write_ok, i2c_write_bad, i2c_read_ok, i2c_read_bad, callback_drops);
    fflush(stdout);
    printf("SOURCE_MODE i2c=%s slave=%s i2c_emitted_ok=%" PRIu32
           " i2c_emitted_bad=%" PRIu32 " topology_active=%d\n",
           i2c_write_only?"write_nack":"full",
           slave_swapped?"sda33_scl26":"sda26_scl33",i2c_emitted_ok,
           i2c_emitted_bad,(int)topology_active);
    fflush(stdout);
    printf("SOURCE_DIAG spi=%s spi_tx_crc=%04x spi_rx_crc=%04x"
           " i2c_write=%s slave_received=%" PRIu32
           " i2c_read=%s slave_prefilled=%" PRIu32 "\n",
           esp_err_to_name(last_spi_error), (unsigned)last_spi_tx_crc, (unsigned)last_spi_rx_crc,
           esp_err_to_name(last_i2c_write_error), last_i2c_received,
           i2c_write_only?"not_attempted":esp_err_to_name(last_i2c_read_error),
           i2c_write_only?0:last_i2c_prefilled);
    fflush(stdout);
}

static void apply_commands(void)
{
    command_t command;
    while (xQueueReceive(command_queue, &command, 0) == pdTRUE) {
        esp_err_t error = ESP_OK;
        if (topology_active && command.kind!='?') {
            printf("ERROR topology busy; retry after TOPOLOGY_END\n");
            continue;
        }
        if (command.kind=='U') {
            error = uart_set_baudrate(UART_NUM_1, command.value);
            if (error==ESP_OK) uart_baud=command.value;
        } else if (command.kind=='S') {
            error = spi_bus_remove_device(spi_device);
            if (error==ESP_OK) {
                error = add_spi_device(command.value);
                if (error==ESP_OK) spi_hz=command.value;
                else ESP_ERROR_CHECK(add_spi_device(spi_hz));
            }
        } else if (command.kind=='I') {
            error = i2c_master_bus_rm_device(master_device);
            if (error==ESP_OK) {
                error = add_master_device(command.value);
                if (error==ESP_OK) i2c_hz=command.value;
                else ESP_ERROR_CHECK(add_master_device(i2c_hz));
            }
        } else if (command.kind=='G') gap_ms=command.value;
        else if (command.kind=='M') {
            error=i2c_master_bus_rm_device(master_device);
            if (error==ESP_OK) {
                bool old=i2c_write_only;
                i2c_write_only=command.value!=0;
                error=add_master_device(i2c_hz);
                if (error!=ESP_OK) { i2c_write_only=old; ESP_ERROR_CHECK(add_master_device(i2c_hz)); }
            }
        } else if (command.kind=='L') {
            error=i2c_del_slave_device(slave_device);
            if (error==ESP_OK) {
                error=add_slave_device(command.value!=0);
                if (error==ESP_OK) slave_swapped=command.value!=0;
                else ESP_ERROR_CHECK(add_slave_device(slave_swapped));
            }
        } else if (command.kind=='T') {
            topology_active=true;
            if (xTaskCreate(topology_task,"topology",4096,NULL,2,NULL)!=pdPASS) {
                topology_active=false; error=ESP_ERR_NO_MEM;
            }
        } else if (command.kind=='P') {
            /* Weak bias only; MISO remains an input, never push-pull output. */
            error=gpio_set_pull_mode(SPI_MISO_GPIO,
                                    command.value?GPIO_PULLUP_ONLY:GPIO_FLOATING);
        }
        printf("COMMAND %c=%" PRIu32 " result=%s\n", command.kind,
               command.value, esp_err_to_name(error));
        print_status();
    }
}

static void console_task(void *arg)
{
    (void)arg;
    char line[48]; size_t used=0;
    for (;;) {
        uint8_t byte;
        if (uart_read_bytes(UART_NUM_0, &byte, 1, pdMS_TO_TICKS(100))!=1)
            continue;
        if (byte=='\r') continue;
        if (byte!='\n') {
            if (used < sizeof(line)-1) line[used++]=(char)byte;
            continue;
        }
        line[used]=0; used=0;
        command_t command = { .kind='?' };
        unsigned long value; char tail;
        if (!strcmp(line, "STATUS")) command.kind='?';
        else if (!strcmp(line,"TOPOLOGY")) command.kind='T';
        else if (!strcmp(line,"I2C_MODE=full")) { command.kind='M'; command.value=0; }
        else if (!strcmp(line,"I2C_MODE=write_nack")) { command.kind='M'; command.value=1; }
        else if (!strcmp(line,"I2C_SLAVE=normal")) { command.kind='L'; command.value=0; }
        else if (!strcmp(line,"I2C_SLAVE=swapped")) { command.kind='L'; command.value=1; }
        else if (!strcmp(line,"MISO_PULL=up")) { command.kind='P'; command.value=1; }
        else if (!strcmp(line,"MISO_PULL=none")) { command.kind='P'; command.value=0; }
        else if (sscanf(line,"UART=%lu%c",&value,&tail)==1 &&
                 (value==115200 || value==1000000)) {
            command.kind='U'; command.value=(uint32_t)value;
        } else if (sscanf(line,"SPI=%lu%c",&value,&tail)==1 &&
                   (value==1000000 || value==5000000 || value==10000000)) {
            command.kind='S'; command.value=(uint32_t)value;
        } else if (sscanf(line,"I2C=%lu%c",&value,&tail)==1 &&
                   (value==100000 || value==400000)) {
            command.kind='I'; command.value=(uint32_t)value;
        } else if (sscanf(line,"GAP=%lu%c",&value,&tail)==1 && value>=1 && value<=100) {
            command.kind='G'; command.value=(uint32_t)value;
        } else { printf("ERROR unsupported command\n"); continue; }
        if (xQueueSend(command_queue,&command,pdMS_TO_TICKS(20))!=pdTRUE)
            printf("ERROR command queue full\n");
    }
}

void app_main(void)
{
    configure_pwm();
    configure_uart();
    configure_spi();
    configure_i2c();
    command_queue = xQueueCreate(4, sizeof(command_t));
    configASSERT(command_queue);
    configASSERT(xTaskCreate(console_task,"console",3072,NULL,3,NULL)==pdPASS);
    print_status();
    int64_t next_status=esp_timer_get_time()+1000000;
    for (;;) {
        apply_commands();
        uint8_t tx[FRAME_SIZE] __attribute__((aligned(4)));
        uint8_t rx[FRAME_SIZE] __attribute__((aligned(4))) = {0};
        frame_make(tx,'U',sequence);
        int sent=uart_write_bytes(UART_NUM_1,tx,FRAME_SIZE);
        if (sent==FRAME_SIZE && uart_wait_tx_done(UART_NUM_1,pdMS_TO_TICKS(100))==ESP_OK)
            uart_ok++;
        else uart_bad++;
        frame_make(tx,'S',sequence);
        spi_transaction_t transaction = { .length=FRAME_SIZE*8,
            .rxlength=FRAME_SIZE*8, .tx_buffer=tx, .rx_buffer=rx };
        esp_err_t error=spi_device_transmit(spi_device,&transaction);
        last_spi_error=error;
        last_spi_tx_crc=frame_crc(tx,FRAME_SIZE);
        last_spi_rx_crc=frame_crc(rx,FRAME_SIZE);
        if (error==ESP_OK && !memcmp(tx,rx,FRAME_SIZE)) spi_ok++;
        else spi_bad++;

        received_t item;
        while (xQueueReceive(received_queue,&item,0)==pdTRUE) { }
        frame_make(tx,'I',sequence);
        error=i2c_master_transmit(master_device,tx,FRAME_SIZE,50);
        last_i2c_write_error=error;
        if (error==ESP_OK) i2c_emitted_ok++;
        else i2c_emitted_bad++;
        last_i2c_received=0;
        if (i2c_write_only) {
            /* No receiver/ACK/read success is inferred from API success with
             * ACK checks disabled. The analyzer must observe actual ACK bits. */
            sequence++;
            if (esp_timer_get_time()>=next_status) {
                print_status(); next_status=esp_timer_get_time()+1000000;
            }
            vTaskDelay(pdMS_TO_TICKS(gap_ms));
            continue;
        }
        if (error==ESP_OK && xQueueReceive(received_queue,&item,pdMS_TO_TICKS(50))==pdTRUE
                && item.length==FRAME_SIZE && !memcmp(tx,item.bytes,FRAME_SIZE)) {
            i2c_write_ok++; last_i2c_received=item.length;
        }
        else i2c_write_bad++;

        frame_make(tx,'R',sequence);
        uint32_t written=0;
        error=i2c_slave_write(slave_device,tx,FRAME_SIZE,&written,50);
        if (error==ESP_OK && written==FRAME_SIZE)
            error=i2c_master_receive(master_device,rx,FRAME_SIZE,50);
        else error=ESP_FAIL;
        last_i2c_read_error=error;
        last_i2c_prefilled=written;
        if (error==ESP_OK && !memcmp(tx,rx,FRAME_SIZE)) i2c_read_ok++;
        else i2c_read_bad++;

        sequence++;
        if (esp_timer_get_time()>=next_status) {
            print_status(); next_status=esp_timer_get_time()+1000000;
        }
        vTaskDelay(pdMS_TO_TICKS(gap_ms));
    }
}
