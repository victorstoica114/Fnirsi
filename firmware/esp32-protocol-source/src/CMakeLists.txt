idf_component_register(SRCS "main.c" INCLUDE_DIRS "."
    REQUIRES esp_driver_ledc esp_driver_uart esp_driver_spi esp_driver_i2c
             esp_driver_gpio esp_timer esp_system freertos)
target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Werror)
