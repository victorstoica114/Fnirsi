/* Classic ESP32 only: fixed hardware PWM on GPIO32, no runtime commands. */
#include <stdint.h>
#include "sdkconfig.h"
#include "esp_attr.h"
#include "soc/soc.h"
#include "soc/dport_reg.h"
#include "soc/gpio_reg.h"
#include "soc/gpio_sig_map.h"
#include "soc/io_mux_reg.h"
#include "soc/ledc_reg.h"
#include "soc/rtc_io_reg.h"

#ifndef PWM_FREQUENCY_HZ
#define PWM_FREQUENCY_HZ 100000UL
#endif

#define APB_FREQUENCY_HZ 80000000UL
#define PWM_INTEGER_DIVIDER (APB_FREQUENCY_HZ / (2UL * PWM_FREQUENCY_HZ))
#define PWM_Q8_DIVIDER (PWM_INTEGER_DIVIDER << 8)

#if defined(CONFIG_PM_ENABLE) || defined(CONFIG_FREERTOS_USE_TICKLESS_IDLE) || \
    defined(CONFIG_ESP_INT_WDT) || defined(CONFIG_ESP_TASK_WDT_EN) || \
    defined(CONFIG_BOOTLOADER_WDT_ENABLE) || defined(CONFIG_SPIRAM)
#error "This controlled PWM experiment requires PM, tickless idle, WDT and PSRAM off."
#endif

_Static_assert(CONFIG_IDF_TARGET_ESP32, "Only the classic ESP32 is supported.");
_Static_assert(CONFIG_FREERTOS_UNICORE, "CPU1 must remain disabled.");
_Static_assert(CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ == 160, "This build uses APB=80 MHz.");
_Static_assert(APB_FREQUENCY_HZ % (2UL * PWM_FREQUENCY_HZ) == 0,
               "Only an exact integer divider is allowed.");
_Static_assert(PWM_INTEGER_DIVIDER >= 1 && PWM_INTEGER_DIVIDER <= 1023,
               "The LEDC Q8 divider must fit the hardware register.");

static void configure_pwm(void)
{
    /* Enable the peripheral APB clock and pulse its reset. */
    REG_SET_BIT(DPORT_PERIP_CLK_EN_REG, DPORT_LEDC_CLK_EN);
    REG_SET_BIT(DPORT_PERIP_RST_EN_REG, DPORT_LEDC_RST);
    REG_CLR_BIT(DPORT_PERIP_RST_EN_REG, DPORT_LEDC_RST);
    REG_WRITE(LEDC_HSCH0_CONF0_REG, LEDC_CLK_EN);
    REG_WRITE(LEDC_INT_ENA_REG, 0);

    /* GPIO32: digital GPIO matrix, 20 mA drive, no pulls or input interrupt. */
    REG_CLR_BIT(RTC_IO_XTAL_32K_PAD_REG,
                RTC_IO_X32P_MUX_SEL | RTC_IO_X32P_HOLD |
                RTC_IO_X32P_RUE | RTC_IO_X32P_RDE);
    REG_WRITE(IO_MUX_GPIO32_REG,
                (FUNC_GPIO32_GPIO32 << MCU_SEL_S) | (2UL << FUN_DRV_S));
    REG_WRITE(GPIO_PIN32_REG, 0);
    REG_WRITE(GPIO_FUNC32_OUT_SEL_CFG_REG,
                LEDC_HS_SIG_OUT0_IDX | GPIO_FUNC32_OEN_SEL);
    REG_WRITE(GPIO_ENABLE1_W1TS_REG, BIT(0));

    /* One-bit counter: two states; one HIGH tick and one LOW tick.
     * DIV_NUM is Q10.8, with all eight fractional bits explicitly zero. */
    const uint32_t timer_configuration =
        LEDC_TICK_SEL_HSTIMER0 |
        (PWM_Q8_DIVIDER << LEDC_DIV_NUM_HSTIMER0_S) |
        (1UL << LEDC_HSTIMER0_DUTY_RES_S);
    REG_WRITE(LEDC_HSTIMER0_CONF_REG,
                timer_configuration | LEDC_HSTIMER0_RST | LEDC_HSTIMER0_PAUSE);
    REG_WRITE(LEDC_HSCH0_HPOINT_REG, 0);
    REG_WRITE(LEDC_HSCH0_DUTY_REG, 1UL << 4);
    /* Constant duty: one update, zero fade step, no ISR. */
    REG_WRITE(LEDC_HSCH0_CONF1_REG,
                LEDC_DUTY_START_HSCH0 | LEDC_DUTY_INC_HSCH0 |
                (1UL << LEDC_DUTY_NUM_HSCH0_S) |
                (1UL << LEDC_DUTY_CYCLE_HSCH0_S));
    REG_WRITE(LEDC_HSCH0_CONF0_REG, LEDC_CLK_EN | LEDC_SIG_OUT_EN_HSCH0);
    REG_WRITE(LEDC_HSTIMER0_CONF_REG, timer_configuration);
}

static void IRAM_ATTR __attribute__((noreturn)) run_without_interrupts(void)
{
    uint32_t previous_ps;
    /* Mask normal CPU interrupts after SDK startup and PWM configuration.
     * No FreeRTOS tick, UART service, task switch or PWM ISR follows. */
    __asm__ volatile("rsil %0, 15\n\trsync" : "=r"(previous_ps) : : "memory");
    (void) previous_ps;
    for (;;) {
        __asm__ volatile("nop");
    }
}

void app_main(void)
{
    configure_pwm();
    run_without_interrupts();
}
