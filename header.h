#include <Wire.h>
#include <IRremote.h>
#include <AccelStepper.h>
#include <PID_v1.h>
#include <PID_AutoTune_v0.h>
#include "MPU6050.h"

// === Définition des pins ===
#define STEP_PIN_L 5
#define DIR_PIN_L  6
#define ENA_PIN_L  4   // ENABLE moteur gauche

#define STEP_PIN_R 9
#define DIR_PIN_R  10
#define ENA_PIN_R  8   // ENABLE moteur droit

uint32_t cmd = 0;

// === Création des moteurs ===
AccelStepper motorL(AccelStepper::DRIVER, STEP_PIN_L, DIR_PIN_L);
AccelStepper motorR(AccelStepper::DRIVER, STEP_PIN_R, DIR_PIN_R);


// === MPU6050 ===
MPU6050 mpu;
unsigned long lastTime;
double angle = 0;      // angle calculé
double gyroRate = 0;   // vitesse angulaire

// === PID ===
double Input, Output, Setpoint = 0;  // On veut rester droit = 0°
double Kp = 1, Ki = 0, Kd = 0;
PID myPID(&Input, &Output, &Setpoint, Kp, Ki, Kd, DIRECT);

// === AutoTune ===
PID_ATune aTune(&Input, &Output);
boolean tuning = true;
double aTuneStep = 50;
double aTuneNoise = 1;
double aTuneStartValue = 100;
unsigned int aTuneLookBack = 20;


// === Commandes utilisateur ===
int moveCmd = 0;  // -1 = reculer, 0 = stop, 1 = avancer
int turnCmd = 0;  // -1 = gauche, 0 = tout droit, 1 = droite