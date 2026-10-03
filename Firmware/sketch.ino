#include <Arduino.h>
#include <DHT.h>
#include <BLEDevice.h>

#define DHT_PIN        4
#define DHT_TYPE       DHT22
#define SLEEP_SECONDS  20     // demo value; real node: 600-1200 (10-20 min)
#define ADV_MS         1000   // advertise for 1 s, then sleep
#define ENABLE_BLE     1      // set to 0 if the simulator hangs on BLE init

// Arduino core 2.x uses std::string, 3.x uses String
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
typedef String BleStr;
#else
typedef std::string BleStr;
#endif

RTC_DATA_ATTR uint16_t bootCount = 0;   // survives deep sleep

DHT dht(DHT_PIN, DHT_TYPE);

void advertise(int16_t t100, uint16_t h100) {
#if ENABLE_BLE
  BLEDevice::init("SolarNode");

  // Manufacturer data: company ID 0xFFFF (test), temp*100, hum*100, counter
  BleStr p;
  p += (char)0xFF; p += (char)0xFF;
  p += (char)(t100 & 0xFF);  p += (char)((t100 >> 8) & 0xFF);
  p += (char)(h100 & 0xFF);  p += (char)((h100 >> 8) & 0xFF);
  p += (char)(bootCount & 0xFF); p += (char)((bootCount >> 8) & 0xFF);

  BLEAdvertisementData adv;
  adv.setFlags(0x06);
  adv.setName("SolarNode");
  adv.setManufacturerData(p);

  BLEAdvertising *a = BLEDevice::getAdvertising();
  a->setAdvertisementData(adv);
  a->setScanResponse(false);
  a->start();
  delay(ADV_MS);
  a->stop();
#endif
}

void setup() {
  Serial.begin(115200);
  delay(100);
  bootCount++;
  Serial.printf("\n--- Wake #%u ---\n", bootCount);

  dht.begin();
  delay(2000);                       // DHT22 needs ~2 s before a valid read
  float t = dht.readTemperature();
  float h = dht.readHumidity();

  if (isnan(t) || isnan(h)) {
    Serial.println("Sensor read failed, skipping advertise");
  } else {
    int16_t  t100 = (int16_t)lroundf(t * 100);
    uint16_t h100 = (uint16_t)lroundf(h * 100);
    Serial.printf("T=%.2f C  H=%.2f %%  -> payload T100=%d H100=%u\n", t, h, t100, h100);
    advertise(t100, h100);
    Serial.println("Advertised");
  }

  Serial.printf("Sleeping %d s\n", SLEEP_SECONDS);
  Serial.flush();
  esp_sleep_enable_timer_wakeup((uint64_t)SLEEP_SECONDS * 1000000ULL);
  esp_deep_sleep_start();
}

void loop() {}