# Smart Cooking Assistant

This repository contains the final integrated software and firmware developed for my final year Smart Cooking Assistant project.

The project combines a Raspberry Pi 5 and an STM32 microcontroller to create a prototype cooking assistant that monitors food using computer vision, mass sensing, and dual temperature sensing. The Raspberry Pi handles the live user interface, backend logic, cooking state monitoring, and communication with the embedded controller. The STM32 is responsible for low level sensing, telemetry, and safety related supervision.

## Project Overview

The aim of the project was to develop a low cost intelligent cooking assistant capable of monitoring cooking conditions in real time and presenting useful feedback through a live interface. The system was designed as an integrated prototype rather than a finished consumer product.

Key features of the project include:

- live user interface for system status and cooking feedback
- Raspberry Pi backend for sensor fusion, cooking logic, and logging
- STM32 firmware for embedded monitoring and safety supervision
- dual PT1000 temperature sensing through MAX31865 interface boards
- mass sensing using an HX711 load cell amplifier
- camera based food and stage recognition support
- fault handling, telemetry, and safe state behaviour

## Repository Contents

### Main Raspberry Pi application
- `app.py`  
  Streamlit based live user interface

- `daemon.py`  
  Main backend service handling telemetry, mass sensing, cooking logic, logging, and API communication

### Vision and camera tools
- `cam_controls.py`  
  Camera control helper functions

- `roi_calibrate.py`  
  Region of interest calibration tool for the cooking area

- `capture_dataset.py`  
  Script used for dataset capture and labelling support

### Calibration and support tools
- `calibrate_trusted.py`  
  HX711 calibration script using trusted masses

- `mass_runtime.py`  
  Runtime mass reading and validation tool

- `stm_logger.py`  
  UART telemetry logging tool for STM32 communication testing

- `hx711_cal.json`  
  Example calibration file used by the mass sensing pipeline

### STM32 firmware
- `main.c`  
  Main embedded firmware implementing sensor reading, telemetry, watchdog behaviour, and safety logic

- `main.h`  
  Main firmware header definitions

- `stm32f4xx_hal_msp.c`  
  Peripheral initialisation and hardware support configuration

- `stm32f4xx_it.c`  
  Interrupt service routines

- `stm32f4xx_it.h`  
  Interrupt handler declarations

- `stm32f4xx_hal_conf.h`  
  HAL configuration

- `system_stm32f4xx.c`  
  STM32 system support file

- `startup_stm32f401retx.s`  
  Startup file for STM32F401RE target

## Notes

This repository is provided as a technical appendix to support the implementation described in the final project report.

It is intended to demonstrate the structure and integration of the final software and firmware used in the project. Some supporting scripts are included for calibration, testing, and development purposes.

## Hardware Summary

The implemented prototype uses:

- Raspberry Pi 5
- STM32 Nucleo F401RE
- Raspberry Pi Camera Module 3
- HX711 load cell amplifier and load cell
- dual PT1000 temperature sensors
- dual MAX31865 RTD interface boards

## Disclaimer

This repository represents an academic prototype developed for a final year project. It is not intended to be a finished commercial cooking product.
