//#include "imu_helpers.h"
#include "SparkFun_BNO08x_Arduino_Library.h"
#include <FastAccelStepper.h>
#include <IRremote.h>
#include <PID_v1.h>


// === Hardware Data ===
#define WHEEL_DIAMETER 20
#define STEPS_REV 200
#define MICRO_STEPS 2


// === Définition des pins ===

#define STEP_PIN_L 7   // CLK
#define DIR_PIN_L  5   // CW
#define ENA_PIN_L  3   // ENABLE moteur gauche

#define STEP_PIN_R 8  // CLK
#define DIR_PIN_R  6  // CW
#define ENA_PIN_R  4  // ENABLE moteur droit


// === Création des moteurs ===
FastAccelStepperEngine engine = FastAccelStepperEngine();
FastAccelStepper *motorL = NULL;
FastAccelStepper *motorR = NULL;



// === BNO085 ===

BNO08x imu;

#define BNO08X_INT  A4
#define BNO08X_RST  A5
#define BNO08X_CS 47

// === PID ===


#define EQUILIBRE -4

double inputA, outputA, setpointA = EQUILIBRE;
double KpA = 21, KiA = 0.0, KdA = 0.9;
PID pidA(&inputA, &outputA, &setpointA, KpA, KiA, KdA, REVERSE);

double inputV, outputV, currentMoveCmd = 0;
double KpV = 0.3, KiV = 0.0, KdV = 0.0;
PID pidV(&inputV, &outputV, &currentMoveCmd, KpV, KiV, KdV, DIRECT);


// === Commandes utilisateur ===
#define IR_PIN 46
uint32_t cmd = 0;
int moveCmd = 0;  // -1 = reculer, 0 = stop, 1 = avancer
int turnCmd = 0;  // -1 = gauche, 0 = tout droit, 1 = droite
double currentTurnCmd = 0;
// int currentMoveCmd = 0;
double balRate = 1;
unsigned long lastCmdTime = 0;
unsigned long lastRemote = 0;
unsigned long move1Start = 0;
unsigned long turn1Start = 0;
unsigned long move2Start = 0;
unsigned long turn2Start = 0;


// Tracking Sensor
const int LEFT_SENSOR_PIN = 49;
const int RIGHT_SENSOR_PIN = 48;
const int CENTER_SENSOR_PIN = 40;
int leftValue;
int rightValue;
int centerValue;


// Reperage des stations
int currentStation = 0;   // station détectée actuellement
int lastSentStation = 0;  // dernière station envoyée au Pi
bool lineFollowingMode = true;

