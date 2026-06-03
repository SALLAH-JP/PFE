void setReports(void) {
  Serial.println("Setting desired reports");
  if (imu.enableGameRotationVector(10) == true) {
    Serial.println(F("Rotation vector enabled"));
    Serial.println(F("Output in form roll, pitch, yaw"));
    delay(100);
  }
}

float lowPassFilter(float value, float prevValue, float alpha) {
  return alpha * value + (1 - alpha) * prevValue;
}

void remoteControl() {
  // **on décode réellement ici**  
  if (IrReceiver.decode()) {

    cmd = IrReceiver.decodedIRData.command;

    if ( cmd == 0x18 ) move1Start = millis();
    else if ( cmd == 0x08 ) turn1Start = millis();
    else if ( cmd == 0x5A ) turn2Start = millis();
    else if ( cmd == 0x52 ) move2Start = millis();
  

    else if ( ( (IrReceiver.decodedIRData.flags & IRDATA_FLAGS_IS_REPEAT) && (millis() - lastRemote > 100) ) || !(IrReceiver.decodedIRData.flags & IRDATA_FLAGS_IS_REPEAT) ) {
    
      bool changed = true;

      if ( cmd == 0x45 ) KpA += 1;
      else if ( cmd == 0x46 ) KiA += 10;
      else if ( cmd == 0x47 ) KdA += 0.1;
      else if ( cmd == 0x44 ) KpA -= 1;
      else if ( cmd == 0x40 ) KiA -= 10;
      else if ( cmd == 0x43 ) KdA -= 0.1;
      else if ( cmd == 0x07 ) KpV += 0.05;
      else if ( cmd == 0x15 ) KiV += 0.005;
      else if ( cmd == 0x09 ) KdV += 0.005;
      else if ( cmd == 0x16 ) KpV -= 0.1;
      else if ( cmd == 0x19 ) KiV -= 0.005;
      else if ( cmd == 0x0D ) KdV -= 0.005;
      else changed = false;


      if (changed) {
         // Appliquer immédiatement
         pidA.SetTunings(KpA, KiA, KdA);
         pidV.SetTunings(KpV, KiV, KdV);
      }


      
    }
    lastRemote = millis();
    IrReceiver.resume();
  }

  if ( millis() - move1Start < 100 ) moveCmd = 200;
  else if ( millis() - move2Start < 100 ) moveCmd = -200;
  else if ( millis() - turn1Start < 100 ) turnCmd = 200;
  else if ( millis() - turn2Start < 100 ) turnCmd = -200;
}


void setMotors(double cmd, int turn) {
  if (abs(cmd) < 50 && turn == 0) {
    motorL->stopMove();
    motorR->stopMove();
    return;  // on sort immédiatement, setSpeedInHz n'est jamais appelé
  }

  double left  = cmd - turn;
  double right = cmd + turn;

  // Ici abs(cmd) >= 50 donc left/right ne peuvent pas être 0
  motorL->setSpeedInHz((int)abs(left));
  motorR->setSpeedInHz((int)abs(right));

  if (left  < 0) motorL->runBackward(); else motorL->runForward();
  if (right <= 0) motorR->runForward();  else motorR->runBackward();
}


float measureSpeed() {
  static long lastPosL = 0;
  static long lastPosR = 0;
  static unsigned long lastTime = 0;
  static float filteredSpeed = 0;

  long posL = motorL->getCurrentPosition();
  long posR = -motorR->getCurrentPosition();
  long deltaStepsL = posL - lastPosL;
  long deltaStepsR = posR - lastPosR;
  unsigned long now = millis();
  //Serial.print(deltaStepsL); Serial.print(" => "); Serial.println(deltaStepsR);

  unsigned long deltaT = now - lastTime; // en ms

  lastPosL = posL;
  lastPosR = posR;
  lastTime = now;

  if (deltaT == 0) return 0; // éviter la division par 0

  float revsPerSecL = ((deltaStepsL * 1000.0) / deltaT) / (STEPS_REV * MICRO_STEPS);
  float revsPerSecR = ((deltaStepsR * 1000.0) / deltaT) / (STEPS_REV * MICRO_STEPS);
  float wheelSpeedL = (PI * WHEEL_DIAMETER) * revsPerSecL;
  float wheelSpeedR = (PI * WHEEL_DIAMETER) * revsPerSecR;
  float speed = (wheelSpeedL + wheelSpeedR) / 2.0;

  // filtrage passe-bas
  filteredSpeed = lowPassFilter(speed, filteredSpeed, 0.3);

  return filteredSpeed ; // [cm/s]
}


void lineTracking() {
  leftValue   = digitalRead(LEFT_SENSOR_PIN);
  rightValue  = digitalRead(RIGHT_SENSOR_PIN);
  centerValue = digitalRead(CENTER_SENSOR_PIN);

  if (centerValue == HIGH) {
    // Ligne centrale : avancer
    //moveCmd = 60;
    turnCmd = 0;
  }
  else if (leftValue == HIGH && rightValue == HIGH) {
    moveCmd = 0;
    turnCmd = 0;
  }
  else if (leftValue == HIGH) {
    // Trop à droite : reculer + tourner pour se recadrer
    moveCmd = 0;
    turnCmd = 75;
  }
  else if (rightValue == HIGH) {
    // Trop à gauche : reculer + tourner pour se recadrer
    moveCmd = 0;
    turnCmd = -75;
  }
  else {
    // Rien : stop
    //moveCmd = 60;
    turnCmd = 0;
  }

  // Détection station : front montant uniquement
  static int prevStation = LOW;
  int allHigh = (leftValue == HIGH && centerValue == HIGH && rightValue == HIGH);

  if ( allHigh && prevStation == LOW ) {
    currentStation = (currentStation + 6) % 7;
  }
  prevStation = allHigh;
}

unsigned long lastTime = 0;
unsigned long loopTime = 0;

void temps() {
  unsigned long now = micros();       // Temps actuel en µs
  loopTime = now - lastTime;          // Durée d'une itération
  lastTime = now;

  // Affiche toutes les 500 ms pour ne pas saturer la liaison série
  static unsigned long lastPrint = 0;
  if (millis() - lastPrint > 500) {
    Serial.print("Loop time (us): ");
    Serial.print(loopTime);
    Serial.print("  =>  Freq: ");
    Serial.print(1000000.0 / loopTime);
    Serial.println(" Hz");
    lastPrint = millis();
  }
}


void readSerialCommand() {
  static char buf[32];
  static int  idx = 0;

  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      buf[idx] = '\0';
      idx = 0;

      // Parse "C:move:turn"
      if (buf[0] == 'C' && buf[1] == ':') {
        int m, t;
        if (sscanf(buf + 2, "%d:%d", &m, &t) == 2) {
          moveCmd = m;
          turnCmd = t;
        }
      }

      // Changement de mode : "M:0" ou "M:1"
      else if (buf[0] == 'M' && buf[1] == ':') {
        lineFollowingMode = (buf[2] == '1');
        Serial.print("MODE:");
        Serial.println(lineFollowingMode ? "LINE" : "MANUAL");
      }


      // Envoie station si changement
      sendStationIfChanged();

    } else if (idx < 31) {
      buf[idx++] = c;
    }
  }
}

void sendStationIfChanged() {

  if (currentStation != lastSentStation) {
    Serial.print("S:");
    Serial.println(currentStation);
    lastSentStation = currentStation;
  }
}


// Callback appelé par NewPing quand l'écho revient (ou timeout)
void echoCheck() {
  if (sonar[us_currentSensor].check_timer()) {
    // écho reçu : convertir en cm
    us_dist[us_currentSensor] = sonar[us_currentSensor].ping_result / US_ROUNDTRIP_CM;
  }
}

void setupUltrasons() {
  us_pingTimer[0] = millis() + 75;          // démarre le 1er ping bientôt
  for (int i = 1; i < US_COUNT; i++)
    us_pingTimer[i] = us_pingTimer[i - 1] + PING_INTERVAL;
}

// Gestion non-bloquante : déclenche les capteurs à tour de rôle via timer
void updateUltrasons() {
  for (int i = 0; i < US_COUNT; i++) {
    if (millis() >= us_pingTimer[i]) {
      us_pingTimer[i] += PING_INTERVAL * US_COUNT;   // reprogramme ce capteur
      sonar[us_currentSensor].timer_stop();          // arrête le précédent
      us_currentSensor = i;
      sonar[i].ping_timer(echoCheck);                // ping non-bloquant + callback
    }
  }
}

// Envoi des 3 distances au Pi : "U:centre:gauche:droite"
void sendUltrasons() {
  static unsigned long lastSend = 0;
  if (millis() - lastSend > 100) {   // 10 Hz
    Serial.print("U:");
    Serial.print(us_dist[US_C], 0); Serial.print(":");
    Serial.print(us_dist[US_L], 0); Serial.print(":");
    Serial.println(us_dist[US_R], 0);
    lastSend = millis();
  }
}
