/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2025 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include <stdio.h>
#include <string.h>
#include <math.h>
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */
/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */
/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
SPI_HandleTypeDef hspi2;

UART_HandleTypeDef huart1;
UART_HandleTypeDef huart2;

/* USER CODE BEGIN PV */

// Sensor 1 (CS on PB5)
volatile uint16_t lastRTDRaw1      = 0;
volatile uint16_t lastRTDRaw16_1   = 0;
volatile uint8_t  lastConfig1      = 0;
volatile uint8_t  lastFault1       = 0;
volatile float    lastTempC1       = 0.0f;

// Sensor 2 (CS on PB6)
volatile uint16_t lastRTDRaw2      = 0;
volatile uint16_t lastRTDRaw16_2   = 0;
volatile uint8_t  lastConfig2      = 0;
volatile uint8_t  lastFault2       = 0;
volatile float    lastTempC2       = 0.0f;

// Combined pan temperature + safety state
volatile float    avgTempC         = 0.0f;
volatile uint8_t  safetyFault      = 0;    // 1 = sensor disagreement / invalid / over-temp
volatile uint8_t  heaterEnabled    = 0;    // logical heater command
volatile uint8_t  emergencyStop    = 0;    // 1 = external E-stop active (or wire broken)


// -------- UART1 (Pi link) --------
static uint8_t  uart1_rx_byte;
static char     uart1_line[64];
static uint8_t  uart1_line_len = 0;

static volatile uint32_t lastHbMs   = 0;
static volatile uint8_t  commsLost  = 1;   // start SAFE until Pi heartbeat arrives
static volatile uint8_t  reqNow     = 0;
static volatile uint8_t overtempLatched = 0;

static uint32_t txSeq = 0;

// Cache the last computed state so REQ can resend instantly
static volatile uint8_t  lastT1Valid = 0;
static volatile uint8_t  lastT2Valid = 0;
static volatile uint8_t  lastHeaterOut = 0;
static volatile uint32_t lastFaultFlags = 0;

// ---- Command-controlled (software) E-stop + ack ----
static volatile uint8_t  estopCmd = 0;   // 1 if asserted by CMD ESTOP
static volatile uint8_t  ackNow   = 0;   // set when CMD ACK received

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_USART2_UART_Init(void);
static void MX_SPI2_Init(void);
static void MX_USART1_UART_Init(void);
/* USER CODE BEGIN PFP */
/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */

// ---- Safety / heater configuration ----

// Virtual heater output (drive an external LED on PB0 to show "heater ON")
#define HEATER_PORT GPIOB
#define HEATER_PIN  GPIO_PIN_0

// Fault / E-STOP status LED on PB1
#define STATUS_LED_PORT GPIOB
#define STATUS_LED_PIN  GPIO_PIN_1

// External industrial E-stop (NC contact between PA1 and GND, active when open)
#define ESTOP_PORT GPIOA
#define ESTOP_PIN  GPIO_PIN_1

// Safety thresholds (°C) - adjust as needed
#define TEMP_TRIP_C   50.0f   // trip at/above this
#define TEMP_RESET_C  45.0f   // clear when at/below this (hysteresis)
#define SENSOR_DIFF_MAX_C    10.0f   // max allowed difference between sensors
#define TEMP_MIN_VALID_C    -40.0f   // sanity range for sensors
#define TEMP_MAX_VALID_C    350.0f

// MAX31865 constants (PT1000)
#define RREF        4300.0f     // reference resistor in ohms
#define RTD_NOMINAL 1000.0f     // PT1000 nominal resistance at 0°C
#define ALPHA       0.00385f    // PT1000 temperature coefficient

// Chip-select assignments
#define MAX1_CS_PORT GPIOB
#define MAX1_CS_PIN  GPIO_PIN_6   // first MAX31865 (sensor 1)

#define MAX2_CS_PORT GPIOB
#define MAX2_CS_PIN  GPIO_PIN_5   // second MAX31865 (sensor 2)

static void MAX31865_WriteRegister(GPIO_TypeDef *csPort, uint16_t csPin,
                                   uint8_t addr_w, uint8_t value)
{
    uint8_t tx[2];
    tx[0] = addr_w;   // e.g. 0x80 for config write
    tx[1] = value;    // data byte

    HAL_GPIO_WritePin(csPort, csPin, GPIO_PIN_RESET);   // CS low
    HAL_SPI_Transmit(&hspi2, tx, 2, 100);
    HAL_GPIO_WritePin(csPort, csPin, GPIO_PIN_SET);     // CS high
}

static uint8_t MAX31865_ReadRegister(GPIO_TypeDef *csPort, uint16_t csPin,
                                     uint8_t addr_r)
{
    uint8_t tx[2] = { addr_r, 0x00 };
    uint8_t rx[2] = { 0, 0 };

    HAL_GPIO_WritePin(csPort, csPin, GPIO_PIN_RESET);   // CS low
    HAL_StatusTypeDef st = HAL_SPI_TransmitReceive(&hspi2, tx, rx, 2, 100);
    HAL_GPIO_WritePin(csPort, csPin, GPIO_PIN_SET);     // CS high

    if (st != HAL_OK)
    {
        return 0xFF;
    }

    // rx[0] = junk during address, rx[1] = register contents
    return rx[1];
}

static uint16_t MAX31865_ReadRTDRaw(GPIO_TypeDef *csPort, uint16_t csPin,
                                    uint16_t *raw16Out)
{
    uint8_t tx[3] = { 0x01, 0x00, 0x00 };   // start at RTD MSB (0x01)
    uint8_t rx[3] = { 0 };

    HAL_GPIO_WritePin(csPort, csPin, GPIO_PIN_RESET);   // CS low
    HAL_StatusTypeDef st = HAL_SPI_TransmitReceive(&hspi2, tx, rx, 3, 100);
    HAL_GPIO_WritePin(csPort, csPin, GPIO_PIN_SET);     // CS high

    if (st != HAL_OK)
    {
        if (raw16Out) *raw16Out = 0xFFFF;
        return 0xFFFF;
    }

    // rx[1] = MSB, rx[2] = LSB
    uint16_t raw16 = ((uint16_t)rx[1] << 8) | rx[2];
    if (raw16Out) *raw16Out = raw16;

    // LSB bit0 is the fault flag; shift away to get 15-bit ADC value
    uint16_t raw = raw16 >> 1;
    return raw;
}

static float MAX31865_RawToResistance(uint16_t raw)
{
    float ratio = (float)raw / 32768.0f;   // 15-bit full scale
    return ratio * RREF;
}

static float MAX31865_RawToCelsius(uint16_t raw)
{
    float R = MAX31865_RawToResistance(raw);
    // Simple linear approximation around 0°C
    return (R - RTD_NOMINAL) / (ALPHA * RTD_NOMINAL);
}

// ---- Fault flag bits for telemetry ----
#define FF_T1_INVALID      (1u << 0)
#define FF_T2_INVALID      (1u << 1)
#define FF_SENSOR_DISAGREE (1u << 2)
#define FF_OVERTEMP        (1u << 3)
#define FF_ESTOP           (1u << 4)
#define FF_COMMS_LOST      (1u << 5)
#define FF_SAFETY_FAULT    (1u << 6)

static void UART1_StartRx(void);
static void SCA_HandleLine(const char *line);
static void SCA_SendTelemetry(uint32_t now_ms,
                              uint8_t t1_valid, uint8_t t2_valid,
                              uint32_t fault_flags, uint8_t heater_out);

static uint8_t SCA_ChecksumXor(const char *s, size_t len)
{
    uint8_t x = 0;
    for (size_t i = 0; i < len; i++) x ^= (uint8_t)s[i];
    return x;
}

// format float temp as "xx.yy" WITHOUT printf-float support
static void SCA_FormatTemp2dp(char *out, size_t out_sz, float tempC)
{
    int32_t v = (int32_t)lroundf(tempC * 100.0f);   // centi-degC
    int32_t ip = v / 100;
    int32_t fp = v % 100;
    if (fp < 0) fp = -fp;
    snprintf(out, out_sz, "%ld.%02ld", (long)ip, (long)fp);
}

static void UART1_StartRx(void)
{
    uart1_line_len = 0;
    (void)HAL_UART_Receive_IT(&huart1, &uart1_rx_byte, 1);
}

static void SCA_HandleLine(const char *line)
{
    if (strcmp(line, "HB") == 0)
    {
        lastHbMs = HAL_GetTick();
        commsLost = 0;
        return;
    }

    if (strcmp(line, "REQ") == 0)
    {
        reqNow = 1;
        return;
    }

    // ----- New commands from Pi -----
    // CMD ESTOP 1   / CMD ESTOP 0
    if (strncmp(line, "CMD ESTOP", 9) == 0)
    {
        // Accept formats like:
        // "CMD ESTOP 1" or "CMD ESTOP=1"
        const char *p = line + 9;
        while (*p == ' ') p++;
        if (*p == '=') { p++; while (*p == ' ') p++; }

        if (*p == '1')
        {
            estopCmd = 1;
        }
        else if (*p == '0')
        {
            estopCmd = 0;
        }
        return;
    }

    // CMD RESET_FAULTS
    if (strcmp(line, "CMD RESET_FAULTS") == 0)
    {
        // Clear latched faults you control in firmware.
        safetyFault = 0;

        // Choose behaviour:
        // Option 1 (recommended for demo): allow reset to clear software estop too
        estopCmd = 0;

        overtempLatched = 0;

        // Note: commsLost clears only via HB
        // Overtemp clears automatically when temp returns safe
        return;
    }

    // CMD ACK
    if (strcmp(line, "CMD ACK") == 0)
    {
        ackNow = 1;   // you can use this later (e.g. clear a buzzer, UI indicator)
        return;
    }

    // Unknown commands ignored
}

static void SCA_SendTelemetry(uint32_t now_ms,
                              uint8_t t1_valid, uint8_t t2_valid,
                              uint32_t fault_flags, uint8_t heater_out)
{
    char t1s[16], t2s[16];
    SCA_FormatTemp2dp(t1s, sizeof(t1s), lastTempC1);
    SCA_FormatTemp2dp(t2s, sizeof(t2s), lastTempC2);

    char payload[180];
    int n = snprintf(payload, sizeof(payload),
        "SCA,%lu,%lu,%s,%s,%u,%u,0x%08lX,%u",
        (unsigned long)txSeq++,
        (unsigned long)now_ms,
        t1s, t2s,
        (unsigned)t1_valid, (unsigned)t2_valid,
        (unsigned long)fault_flags,
        (unsigned)heater_out
    );

    if (n <= 0) return;

    uint8_t cs = SCA_ChecksumXor(payload, (size_t)n);

    char frame[200];
    int m = snprintf(frame, sizeof(frame), "%s*%02X\n", payload, (unsigned)cs);
    if (m <= 0) return;

    (void)HAL_UART_Transmit(&huart1, (uint8_t*)frame, (uint16_t)m, 20);
}

// UART RX ISR callback (line-based)
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART1)
    {
        char c = (char)uart1_rx_byte;

        if (c == '\r')
        {
            // ignore CR
        }
        else if (c == '\n')
        {
            uart1_line[uart1_line_len] = '\0';
            if (uart1_line_len > 0) SCA_HandleLine(uart1_line);
            uart1_line_len = 0;
        }
        else
        {
            if (uart1_line_len < (sizeof(uart1_line) - 1))
                uart1_line[uart1_line_len++] = c;
            else
                uart1_line_len = 0; // overflow -> reset
        }

        (void)HAL_UART_Receive_IT(&huart1, &uart1_rx_byte, 1);
    }
}

// Optional: recover RX after UART errors
void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART1)
    {
        UART1_StartRx();
    }
}

/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */
  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */
  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */
  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_USART2_UART_Init();   // keep for ST-LINK VCP/debug
  MX_USART1_UART_Init();   // new: Pi link on PA9/PA10
  MX_SPI2_Init();
  UART1_StartRx();
  /* USER CODE BEGIN 2 */
  // Idle CS high at startup
  HAL_GPIO_WritePin(MAX1_CS_PORT, MAX1_CS_PIN, GPIO_PIN_SET);  // CS1 high (PB5)
  HAL_GPIO_WritePin(MAX2_CS_PORT, MAX2_CS_PIN, GPIO_PIN_SET);  // CS2 high (PB6)

  // Ensure heater output is OFF at startup (fail-safe)
    heaterEnabled = 0;
    safetyFault   = 0;
    emergencyStop = 0;
    commsLost     = 1;                 // start SAFE until HB arrives
    lastHbMs      = HAL_GetTick();     // start timer now


  HAL_GPIO_WritePin(HEATER_PORT, HEATER_PIN, GPIO_PIN_RESET);
  HAL_GPIO_WritePin(STATUS_LED_PORT, STATUS_LED_PIN, GPIO_PIN_RESET);

  HAL_Delay(100);  // let both MAX31865s power up

  // Force known config: VBIAS on, auto conversion, 50 Hz filter (0xC1)
  MAX31865_WriteRegister(MAX1_CS_PORT, MAX1_CS_PIN, 0x80, 0xC1);
  MAX31865_WriteRegister(MAX2_CS_PORT, MAX2_CS_PIN, 0x80, 0xC1);
  HAL_Delay(2);

  // Clear any latched faults (set the fault-clear bit), then return to normal config
  MAX31865_WriteRegister(MAX1_CS_PORT, MAX1_CS_PIN, 0x80, 0xC3);
  MAX31865_WriteRegister(MAX2_CS_PORT, MAX2_CS_PIN, 0x80, 0xC3);
  HAL_Delay(2);
  MAX31865_WriteRegister(MAX1_CS_PORT, MAX1_CS_PIN, 0x80, 0xC1);
  MAX31865_WriteRegister(MAX2_CS_PORT, MAX2_CS_PIN, 0x80, 0xC1);
  HAL_Delay(10);
  /* USER CODE END 2 */

  /* Infinite loop */
    while (1)
    {
      /* USER CODE BEGIN WHILE */

    	// --- E-STOP sources ---
    	// Hardware estop (NC contact) is currently disabled; keep it for later:
    	// uint8_t hwEstop = (HAL_GPIO_ReadPin(ESTOP_PORT, ESTOP_PIN) == GPIO_PIN_SET);

    	// Commanded estop from Pi:
    	uint8_t hwEstop = 0;              // keep hardware disabled for now
    	emergencyStop = (hwEstop || estopCmd) ? 1 : 0;

      // ---- Heartbeat watchdog: if Pi stops talking, force SAFE ----
      const uint32_t HB_TIMEOUT_MS = 1500;
      if ((HAL_GetTick() - lastHbMs) > HB_TIMEOUT_MS)
      {
        commsLost = 1;
      }

      // --- Read both RTDs every 500 ms ---
      static uint32_t lastReadMs = 0;
      uint32_t now = HAL_GetTick();

      if ((now - lastReadMs) >= 500)
      {
        lastReadMs = now;

        // ----- Sensor 1 -----
        lastConfig1 = MAX31865_ReadRegister(MAX1_CS_PORT, MAX1_CS_PIN, 0x00);
        lastRTDRaw1 = MAX31865_ReadRTDRaw(MAX1_CS_PORT, MAX1_CS_PIN, &lastRTDRaw16_1);
        lastFault1  = MAX31865_ReadRegister(MAX1_CS_PORT, MAX1_CS_PIN, 0x07);
        if (lastFault1 == 0 && lastRTDRaw1 != 0 && lastRTDRaw1 != 0x7FFF)
          lastTempC1 = MAX31865_RawToCelsius(lastRTDRaw1);

        // ----- Sensor 2 -----
        lastConfig2 = MAX31865_ReadRegister(MAX2_CS_PORT, MAX2_CS_PIN, 0x00);
        lastRTDRaw2 = MAX31865_ReadRTDRaw(MAX2_CS_PORT, MAX2_CS_PIN, &lastRTDRaw16_2);
        lastFault2  = MAX31865_ReadRegister(MAX2_CS_PORT, MAX2_CS_PIN, 0x07);
        if (lastFault2 == 0 && lastRTDRaw2 != 0 && lastRTDRaw2 != 0x7FFF)
          lastTempC2 = MAX31865_RawToCelsius(lastRTDRaw2);

        // Validity checks
        uint8_t t1_valid = (lastFault1 == 0 &&
                            lastRTDRaw1 != 0 && lastRTDRaw1 != 0x7FFF &&
                            lastTempC1 > TEMP_MIN_VALID_C && lastTempC1 < TEMP_MAX_VALID_C);

        uint8_t t2_valid = (lastFault2 == 0 &&
                            lastRTDRaw2 != 0 && lastRTDRaw2 != 0x7FFF &&
                            lastTempC2 > TEMP_MIN_VALID_C && lastTempC2 < TEMP_MAX_VALID_C);

        safetyFault = 0;
        float usedTemp = avgTempC;

        if (t1_valid && t2_valid)
        {
          float diff = lastTempC1 - lastTempC2;
          if (diff < 0.0f) diff = -diff;

          usedTemp = 0.5f * (lastTempC1 + lastTempC2);
          if (diff > SENSOR_DIFF_MAX_C) safetyFault = 1;
        }
        else if (t1_valid) usedTemp = lastTempC1;
        else if (t2_valid) usedTemp = lastTempC2;
        else safetyFault = 1;

        avgTempC = usedTemp;

        // ---- Over-temp latch with hysteresis ----
        if (avgTempC >= TEMP_TRIP_C)
        {
          overtempLatched = 1;
        }
        else if (avgTempC <= TEMP_RESET_C)
        {
          overtempLatched = 0;
        }

        // Heater logic (virtual) - forced off if any critical condition
        if (overtempLatched || safetyFault || emergencyStop || commsLost)
        {
          heaterEnabled = 0;
        }
        else
        {
          heaterEnabled = 1;
        }

        if (emergencyStop) heaterEnabled = 0;

        // Actual output gate includes commsLost
        uint8_t heater_out = (heaterEnabled && !safetyFault && !emergencyStop && !commsLost) ? 1 : 0;

        HAL_GPIO_WritePin(HEATER_PORT, HEATER_PIN, heater_out ? GPIO_PIN_SET : GPIO_PIN_RESET);
        HAL_GPIO_WritePin(STATUS_LED_PORT, STATUS_LED_PIN,
                          (safetyFault || overtempLatched || emergencyStop || commsLost) ? GPIO_PIN_SET : GPIO_PIN_RESET);

        // Build flags
        uint32_t fault_flags = 0;
        if (!t1_valid) fault_flags |= FF_T1_INVALID;
        if (!t2_valid) fault_flags |= FF_T2_INVALID;

        if (t1_valid && t2_valid)
        {
          float diff = lastTempC1 - lastTempC2;
          if (diff < 0.0f) diff = -diff;
          if (diff > SENSOR_DIFF_MAX_C) fault_flags |= FF_SENSOR_DISAGREE;
        }

        if (overtempLatched) fault_flags |= FF_OVERTEMP;
        if (emergencyStop)          fault_flags |= FF_ESTOP;
        if (commsLost)              fault_flags |= FF_COMMS_LOST;
        if (safetyFault)            fault_flags |= FF_SAFETY_FAULT;

        // cache for REQ
        lastT1Valid = t1_valid;
        lastT2Valid = t2_valid;
        lastFaultFlags = fault_flags;
        lastHeaterOut = heater_out;

        // send at 2 Hz
        SCA_SendTelemetry(now, t1_valid, t2_valid, fault_flags, heater_out);
      }

      // REQ handled ASAP (not waiting for next 500 ms tick)
      if (reqNow)
      {
        reqNow = 0;
        SCA_SendTelemetry(HAL_GetTick(), lastT1Valid, lastT2Valid, lastFaultFlags, lastHeaterOut);
      }

      // ACK placeholder: clear immediately (so it behaves like a one-shot)
      if (ackNow)
      {
        ackNow = 0;
      }

      HAL_Delay(5);

      /* USER CODE END WHILE */
    }
  }


  /* USER CODE BEGIN 3 */
  /* USER CODE END 3 */


/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure the main internal regulator output voltage
  */
  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE2);

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSI;
  RCC_OscInitStruct.PLL.PLLM = 16;
  RCC_OscInitStruct.PLL.PLLN = 336;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV4;
  RCC_OscInitStruct.PLL.PLLQ = 7;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2) != HAL_OK)
  {
    Error_Handler();
  }
}

/**
  * @brief SPI2 Initialization Function
  * @param None
  * @retval None
  */
static void MX_SPI2_Init(void)
{

  /* USER CODE BEGIN SPI2_Init 0 */

  /* USER CODE END SPI2_Init 0 */

  /* USER CODE BEGIN SPI2_Init 1 */

  /* USER CODE END SPI2_Init 1 */
  /* SPI2 parameter configuration*/
  hspi2.Instance = SPI2;
  hspi2.Init.Mode = SPI_MODE_MASTER;
  hspi2.Init.Direction = SPI_DIRECTION_2LINES;
  hspi2.Init.DataSize = SPI_DATASIZE_8BIT;
  hspi2.Init.CLKPolarity = SPI_POLARITY_LOW;
  hspi2.Init.CLKPhase = SPI_PHASE_2EDGE;
  hspi2.Init.NSS = SPI_NSS_SOFT;
  hspi2.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_2;
  hspi2.Init.FirstBit = SPI_FIRSTBIT_MSB;
  hspi2.Init.TIMode = SPI_TIMODE_DISABLE;
  hspi2.Init.CRCCalculation = SPI_CRCCALCULATION_DISABLE;
  hspi2.Init.CRCPolynomial = 10;
  if (HAL_SPI_Init(&hspi2) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN SPI2_Init 2 */
  // Force safer SPI speed for MAX31865 (CubeMX changed it)
  hspi2.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_32;
  if (HAL_SPI_Init(&hspi2) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE END SPI2_Init 2 */

}

/**
  * @brief USART1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_USART1_UART_Init(void)
{

  /* USER CODE BEGIN USART1_Init 0 */

  /* USER CODE END USART1_Init 0 */

  /* USER CODE BEGIN USART1_Init 1 */

  /* USER CODE END USART1_Init 1 */
  huart1.Instance = USART1;
  huart1.Init.BaudRate = 115200;
  huart1.Init.WordLength = UART_WORDLENGTH_8B;
  huart1.Init.StopBits = UART_STOPBITS_1;
  huart1.Init.Parity = UART_PARITY_NONE;
  huart1.Init.Mode = UART_MODE_TX_RX;
  huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart1.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart1) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN USART1_Init 2 */

  /* USER CODE END USART1_Init 2 */

}

/**
  * @brief USART2 Initialization Function
  * @param None
  * @retval None
  */
static void MX_USART2_UART_Init(void)
{

  /* USER CODE BEGIN USART2_Init 0 */

  /* USER CODE END USART2_Init 0 */

  /* USER CODE BEGIN USART2_Init 1 */

  /* USER CODE END USART2_Init 1 */
  huart2.Instance = USART2;
  huart2.Init.BaudRate = 115200;
  huart2.Init.WordLength = UART_WORDLENGTH_8B;
  huart2.Init.StopBits = UART_STOPBITS_1;
  huart2.Init.Parity = UART_PARITY_NONE;
  huart2.Init.Mode = UART_MODE_TX_RX;
  huart2.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart2.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart2) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN USART2_Init 2 */

  /* USER CODE END USART2_Init 2 */

}

/**
  * @brief GPIO Initialization Function
  * @param None
  * @retval None
  */
static void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};
  /* USER CODE BEGIN MX_GPIO_Init_1 */

  /* USER CODE END MX_GPIO_Init_1 */

  /* GPIO Ports Clock Enable */
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOH_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(LD2_GPIO_Port, LD2_Pin, GPIO_PIN_RESET);

  /*Configure GPIO pin Output Level */
  // was RESET:
  HAL_GPIO_WritePin(GPIOB, GPIO_PIN_5|GPIO_PIN_6, GPIO_PIN_SET);

  /*Configure GPIO pin : PC13 */
  GPIO_InitStruct.Pin = GPIO_PIN_13;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(GPIOC, &GPIO_InitStruct);

  /*Configure GPIO pin : LD2_Pin */
  GPIO_InitStruct.Pin = LD2_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(LD2_GPIO_Port, &GPIO_InitStruct);

  /*Configure GPIO pins : PB5 PB6 */
  GPIO_InitStruct.Pin = GPIO_PIN_5|GPIO_PIN_6;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_PULLUP;//was no pull
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

  /* USER CODE BEGIN MX_GPIO_Init_2 */
  // --- Restore pins used by your logic (CubeMX may not have them enabled) ---

  // PB0 + PB1 outputs (heater + status)
  HAL_GPIO_WritePin(GPIOB, GPIO_PIN_0 | GPIO_PIN_1, GPIO_PIN_RESET);
  GPIO_InitStruct.Pin = GPIO_PIN_0 | GPIO_PIN_1;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

  // PA1 input pull-up (E-stop)
  GPIO_InitStruct.Pin = ESTOP_PIN;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_PULLUP;
  HAL_GPIO_Init(ESTOP_PORT, &GPIO_InitStruct);
  /* USER CODE END MX_GPIO_Init_2 */
}

/* USER CODE BEGIN 4 */

/* USER CODE END 4 */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
