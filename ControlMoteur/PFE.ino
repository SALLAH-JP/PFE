#include "header.h"


void setup() {
  Serial.begin(115200);

  Serial.println("Start Setup");

  // MPU init
  Wire.begin();

  //if (myIMU.begin() == false) {  // Setup without INT/RST control (Not Recommended)
  if (imu.begin(BNO08X_ADDR, Wire, BNO08X_INT, BNO08X_RST) == false) {
    Serial.println("BNO08x not detected at default I2C address. Check your jumpers and the hookup guide. Freezing...");
    while (1)
      ;
  }
  Serial.println("BNO08x found!");

  Wire.setClock(400000);

  setReports();
  Serial.println("Reading events");
  delay(100);

  // IR Receiver Setup and interrupt
  IrReceiver.begin(IR_PIN, ENABLE_LED_FEEDBACK);


  // Activer les moteurs
  engine.init();

  // Moteur droit
  motorR = engine.stepperConnectToPin(STEP_PIN_R);   // STEP droit
  if (motorR) {
    motorR->setDirectionPin(DIR_PIN_R);            // DIR droit
    motorR->setEnablePin(ENA_PIN_R, false);               // EN droit
    motorR->setAutoEnable(true);
    motorR->setAcceleration(3000);
  }

  // Moteur gauche
  motorL = engine.stepperConnectToPin(STEP_PIN_L);   // STEP gauche
  if (motorL) {
    motorL->setDirectionPin(DIR_PIN_L);            // DIR gauche
    motorL->setEnablePin(ENA_PIN_L, false);               // EN gauche
    motorL->setAutoEnable(true);
    motorL->setAcceleration(3000);
  }


  // Tracking Sensor Setup
  pinMode(LEFT_SENSOR_PIN, INPUT);
  pinMode(RIGHT_SENSOR_PIN, INPUT);

  // PID
  pidA.SetSampleTime(10);
  pidA.SetOutputLimits(-5000, 5000);
  pidA.SetMode(AUTOMATIC);

  pidV.SetSampleTime(10);
  pidV.SetOutputLimits(-45, 45);
  pidV.SetMode(AUTOMATIC);


  Serial.println("Setup Completed !");
}


void loop() {
  moveCmd = 0;
  turnCmd = 0;
  
  //remoteControl();
  //pidA.SetTunings(KpA, KiA, KdA);
  //pidV.SetTunings(KpV, KiV, KdV);
  //lineTracking();

  // filtrage passe-bas des commandes
  currentMoveCmd = lowPassFilter(moveCmd, currentMoveCmd, 0.05 );
  currentTurnCmd = lowPassFilter(turnCmd, currentTurnCmd, 0.09 );


  if ( imu.getSensorEvent() == true && imu.getSensorEventID() == SENSOR_REPORTID_ROTATION_VECTOR ) {
    
    inputA = (imu.getPitch()) * 180.0 / PI;
    inputV = measureSpeed();

    pidV.Compute();
    setpointA = EQUILIBRE + outputV;
    pidA.Compute();
    
    //Serial.print(inputA); Serial.print(" => "); Serial.println(outputA);
    //Serial.print(inputV); Serial.print(" => "); Serial.println(outputV);
    
  }


  //Serial.print(inputV); Serial.print(" => "); Serial.print(KpV); Serial.print(" => "); Serial.print(KiV); Serial.print(" => "); Serial.println(KdV);
  //Serial.print(currentMoveCmd); Serial.print(" => "); Serial.println(currentTurnCmd);
  setMotors(outputA + lineFwd, currentTurnCmd);
  temps();

}
