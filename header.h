#include "MPU6050_6Axis_MotionApps20.h"
#include <FastAccelStepper.h>
#include <RGBmatrixPanel.h>
#include <IRremote.h>
#include <PID_v1.h>


// === Hardware Data ===
#define WHEEL_DIAMETER 0.2
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
MPU6050 mpu;
// MPU control/status vars
volatile bool mpuInterrupt = false;
bool dmpReady = false;  // set true if DMP init was successful
uint8_t mpuIntStatus;   // holds actual interrupt status byte from MPU
uint8_t devStatus;      // return status after each device operation (0 = success, !0 = error)
uint16_t packetSize;    // expected DMP packet size (default is 42 bytes)
uint16_t fifoCount;     // count of all bytes currently in FIFO
uint8_t fifoBuffer[64]; // FIFO storage buffer

static float pitch;
Quaternion q;           // [w, x, y, z]         quaternion container
VectorFloat gravity;    // [x, y, z]            gravity vector
float ypr[3];


// === PID ===


#define EQUILIBRE 0
double inputA, outputA, setpointA = EQUILIBRE;
double KpA = 25, KiA = 0, KdA = 0.3;
PID pidA(&inputA, &outputA, &setpointA, KpA, KiA, KdA, REVERSE);

double inputV, outputV, currentMoveCmd = 0;
double KpV = 3, KiV = 0, KdV = 0;
PID pidV(&inputV, &outputV, &currentMoveCmd, KpV, KiV, KdV, DIRECT);


// === Commandes utilisateur ===
#define IR_PIN 18
uint32_t cmd = 0;
int moveCmd = 0;  // -1 = reculer, 0 = stop, 1 = avancer
int turnCmd = 0;  // -1 = gauche, 0 = tout droit, 1 = droite
double currentTurnCmd = 0;
// int currentMoveCmd = 0;
double balRate = 1;
unsigned long lastCmdTime = 0;
unsigned long lastRemote = 0;


// Tracking Sensor
const int LEFT_SENSOR_PIN = 53;
const int RIGHT_SENSOR_PIN = 52;
int leftValue;
int rightValue;

