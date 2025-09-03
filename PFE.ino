#include "header.h"


void setup() {
  Serial.begin(115200);

  Wire.begin();

  // MPU init
  mpu.initialize();
  if (!mpu.testConnection()) {
    Serial.println("Erreur MPU6050 !");
    while (1);
  }
  Serial.println("MPU6050 Found!");
  lastTime = millis();

  // IR Receiver Setup and interrupt
  IrReceiver.begin(2, ENABLE_LED_FEEDBACK);


  // Activer les moteurs
  pinMode(ENA_PIN_L, OUTPUT);
  pinMode(ENA_PIN_R, OUTPUT);
  digitalWrite(ENA_PIN_L, HIGH);  // LOW = activé sur TB6600
  digitalWrite(ENA_PIN_R, HIGH);

  // Config AccelStepper
  motorL.setMaxSpeed(500);      // vitesse max en pas/sec
  motorR.setMaxSpeed(500);

  motorR.setPinsInverted(true, false, false);


  // PID
  myPID.SetOutputLimits(-500, 500);
  myPID.SetMode(MANUAL);

  // AutoTune
  aTune.SetNoiseBand(aTuneNoise);
  aTune.SetOutputStep(aTuneStep);
  aTune.SetLookbackSec((int)aTuneLookBack);
  Serial.println("=== AutoTune en cours... Ne touche pas le robot ===");

}


void loop() {
  remoteControl();

  // Lire angle MPU6050
  updateAngle();
  Input = angle;

  if (tuning) {
    if (aTune.Runtime() != 0) {
      tuning = false;
      Kp = aTune.GetKp();
      Ki = aTune.GetKi();
      Kd = aTune.GetKd();
      myPID.SetTunings(Kp, Ki, Kd);
      myPID.SetMode(AUTOMATIC);

      Serial.println("=== AutoTune terminé ===");
      Serial.print("Kp="); Serial.println(Kp);
      Serial.print("Ki="); Serial.println(Ki);
      Serial.print("Kd="); Serial.println(Kd);
    }
  } else {
    // mettre consigne en fonction des commandes
    if (moveCmd == 1) Setpoint = 2;   // avance
    else if (moveCmd == -1) Setpoint = -2; // recule
    else Setpoint = 0; // stop


    // Calcul PID
    myPID.Compute();
    Serial.print(Input); Serial.print(" =>"); Serial.println(Output);

    setMotors(Output, turnCmd);

  }

  motorL.runSpeed();
  motorR.runSpeed();
}
