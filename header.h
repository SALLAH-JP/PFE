#include <Wire.h>
#include <IRremote.h>
#include <FastAccelStepper.h>
#include <PID_v1.h>
#include <I2Cdev.h>
#include "SparkFun_BNO08x_Arduino_Library.h"


// === Hardware Data ===
#define WHEEL_DIAMETER 0.2
#define STEPS_REV 200
#define MICRO_STEPS 2


// === Définition des pins ===
#define STEP_PIN_L 9
#define DIR_PIN_L  8
#define ENA_PIN_L  7   // ENABLE moteur gauche

#define STEP_PIN_R 10
#define DIR_PIN_R  11
#define ENA_PIN_R  12   // ENABLE moteur droit


// === Création des moteurs ===
FastAccelStepperEngine engine = FastAccelStepperEngine();
FastAccelStepper *motorL = NULL;
FastAccelStepper *motorR = NULL;


// === BNO085 ===
BNO08x imu;

#define BNO08X_INT  A4
#define BNO08X_RST  -1 //A5
#define BNO08X_ADDR 0x4B


// === PID ===
#define EQUILIBRE -7
double inputA, outputA, setpointA = EQUILIBRE;
double KpA = 2, KiA = 0, KdA = 0;
PID pidA(&inputA, &outputA, &setpointA, KpA, KiA, KdA, DIRECT);

double inputV, outputV, setpointV = 0;
double KpV = 0, KiV = 0, KdV = 0;
PID pidV(&inputV, &outputV, &setpointV, KpV, KiV, KdV, DIRECT);


// === Commandes utilisateur ===
uint32_t cmd = 0;
int moveCmd = 0;  // -1 = reculer, 0 = stop, 1 = avancer
int turnCmd = 0;  // -1 = gauche, 0 = tout droit, 1 = droite
unsigned long lastCmdTime = 0;