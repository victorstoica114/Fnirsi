#include <Arduino.h>

// FNK0060 / ESP32-WROVER: camera and SD card absent.
// GPIO32 -> DLA input D0; ESP32 GND -> DLA GND.
// Output starts LOW. Serial commands explicitly enable/disable the PWM.
constexpr uint8_t kOutputPin = 32;
constexpr uint8_t kChannel = 0;
constexpr uint32_t kFrequencyHz = 100000;
constexpr uint8_t kResolutionBits = 8;
constexpr uint32_t kDuty = 128; // 128/256 = 50%.

static bool ready = false;
static bool enabled = false;
static char command[16];
static size_t used = 0;
static bool overflow = false;

static void report()
{
    Serial.printf("DLA_TEST gpio=%u frequency=%lu duty=50 enabled=%u ready=%u\n",
        kOutputPin, static_cast<unsigned long>(kFrequencyHz), enabled, ready);
}

static void execute()
{
    command[used] = '\0';
    if (overflow) {
        Serial.println("ERROR command too long");
    } else if (!strcmp(command, "STATUS")) {
        report();
    } else if (!strcmp(command, "ON") || !strcmp(command, "OFF")) {
        const bool requested = !strcmp(command, "ON");
        if (!ready || !ledcWrite(kOutputPin, requested ? kDuty : 0)) {
            Serial.println("ERROR PWM configuration");
        } else {
            enabled = requested;
            report();
        }
    } else if (used) {
        Serial.println("ERROR expected ON, OFF or STATUS");
    }
    used = 0;
    overflow = false;
}

void setup()
{
    pinMode(kOutputPin, OUTPUT);
    digitalWrite(kOutputPin, LOW);
    Serial.begin(115200);
    ready = ledcSetClockSource(LEDC_USE_APB_CLK)
        && ledcAttachChannel(kOutputPin, kFrequencyHz, kResolutionBits, kChannel)
        && ledcWrite(kOutputPin, 0);
    delay(100);
    Serial.println("FNIRSI test source; GPIO32 is OFF at startup. Commands: ON OFF STATUS");
    report();
}

void loop()
{
    while (Serial.available()) {
        const char ch = static_cast<char>(Serial.read());
        if (ch == '\n') {
            execute();
        } else if (ch != '\r') {
            if (used + 1 < sizeof(command)) {
                command[used++] = ch;
            } else {
                overflow = true;
            }
        }
    }
    delay(1);
}
