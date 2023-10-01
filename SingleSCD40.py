# Import necessary libraries and modules
from flask import Flask, request, render_template
import time
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import configparser
from Adafruit_IO import Client
from threading import Thread
import os
import board
import adafruit_scd4x

# Initialize the Flask web application
app = Flask(__name__)

# Define locations for log files
LOG_FILE = "sensor_readings.log"
ERROR_LOG_FILE = "error_log.log"

# Function to read settings from a configuration file
def read_settings_from_conf(conf_file):
    config = configparser.ConfigParser()
    config.read(conf_file)
    settings = {}
    keys = [
        'SENSOR_LOCATION_NAME', 'MINUTES_BETWEEN_READS', 'SENSOR_THRESHOLD_TEMP',
        'SENSOR_LOWER_THRESHOLD_TEMP', 'THRESHOLD_COUNT', 'SLACK_API_TOKEN',
        'SLACK_CHANNEL', 'ADAFRUIT_IO_USERNAME', 'ADAFRUIT_IO_KEY',
        'ADAFRUIT_IO_GROUP_NAME', 'ADAFRUIT_IO_TEMP_FEED', 'ADAFRUIT_IO_HUMIDITY_FEED',
        'ADAFRUIT_IO_CO2_FEED', 'SENSOR_CO2_THRESHOLD'
    ]
    for key in keys:
        try:
            if key in ['SENSOR_THRESHOLD_TEMP', 'SENSOR_LOWER_THRESHOLD_TEMP', 'SENSOR_CO2_THRESHOLD']:
                settings[key] = float(config.get('General', key))
            elif key in ['MINUTES_BETWEEN_READS', 'THRESHOLD_COUNT']:
                settings[key] = int(config.get('General', key))
            else:
                settings[key] = config.get('General', key)
        except configparser.NoOptionError:
            log_error(f"Missing {key} in configuration file.")
            raise
    return settings

# Function to write settings to a configuration file
def write_settings_to_conf(conf_file, settings):
    config = configparser.ConfigParser()
    config['General'] = settings
    with open(conf_file, 'w') as configfile:
        config.write(configfile)

# Function to log errors to an error log file
def log_error(message):
    with open(ERROR_LOG_FILE, 'a') as file:
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        file.write(f"{timestamp} - ERROR: {message}\n")

# Function to log sensor readings to a log file
def log_to_file(sensor_name, temperature, humidity, co2):
    with open(LOG_FILE, 'a') as file:
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        file.write(f"{timestamp} - {sensor_name} - Temperature: {temperature}°F, Humidity: {humidity}%, CO2: {co2} ppm\n")

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    conf_file = 'SingleSensorSettings.conf'
    if request.method == 'POST':
        action = request.form.get('action')
        new_settings = {key: value for key, value in request.form.items() if key != "action"}
        write_settings_to_conf(conf_file, new_settings)
        if action == "reboot":
            os.system('sudo reboot')
        return 'Settings updated!'
    else:
        current_settings = read_settings_from_conf(conf_file)
        return render_template('settings.html', settings=current_settings)

# Function to continuously monitor sensor readings and send alerts
def run_monitoring():
    settings = read_settings_from_conf('SingleSensorSettings.conf')
    for key, value in settings.items():
        globals()[key] = value

    SENSOR_ABOVE_THRESHOLD_COUNT = 0
    SENSOR_ALERT_SENT = False
    SENSOR_BELOW_THRESHOLD_COUNT = 0
    SENSOR_BELOW_ALERT_SENT = False
    SENSOR_CO2_ABOVE_THRESHOLD_COUNT = 0
    SENSOR_CO2_ALERT_SENT = False

    slack_client = WebClient(token=SLACK_API_TOKEN)
    adafruit_io_client = Client(ADAFRUIT_IO_USERNAME, ADAFRUIT_IO_KEY)

    i2c = board.I2C()
    sensor = adafruit_scd4x.SCD4X(i2c)
    sensor.start_periodic_measurement()

    while True:
        if sensor.data_ready:
            co2 = sensor.CO2
            temperature_celsius = sensor.temperature
            temperature_fahrenheit = (temperature_celsius * 9/5) + 32  # Convert to Fahrenheit
            humidity = sensor.relative_humidity
            log_to_file(SENSOR_LOCATION_NAME, temperature_fahrenheit, humidity, co2)
        
            try:
                adafruit_io_client.send_data(f"{ADAFRUIT_IO_GROUP_NAME}.{ADAFRUIT_IO_TEMP_FEED}", temperature_fahrenheit)
                adafruit_io_client.send_data(f"{ADAFRUIT_IO_GROUP_NAME}.{ADAFRUIT_IO_HUMIDITY_FEED}", humidity)
                adafruit_io_client.send_data(f"{ADAFRUIT_IO_GROUP_NAME}.{ADAFRUIT_IO_CO2_FEED}", co2)
            except Exception as e:
                log_error(f"Failed to send data to Adafruit IO: {e}")

            if temperature_fahrenheit > SENSOR_THRESHOLD_TEMP:
                SENSOR_ABOVE_THRESHOLD_COUNT += 1
                if SENSOR_ABOVE_THRESHOLD_COUNT >= THRESHOLD_COUNT and not SENSOR_ALERT_SENT:
                    slack_client.chat_postMessage(channel=SLACK_CHANNEL, text=f"ALERT: {SENSOR_LOCATION_NAME} temperature above {SENSOR_THRESHOLD_TEMP}°F")
                    SENSOR_ALERT_SENT = True

            elif temperature_fahrenheit < SENSOR_LOWER_THRESHOLD_TEMP:
                SENSOR_BELOW_THRESHOLD_COUNT += 1
                if SENSOR_BELOW_THRESHOLD_COUNT >= THRESHOLD_COUNT and not SENSOR_BELOW_ALERT_SENT:
                    slack_client.chat_postMessage(channel=SLACK_CHANNEL, text=f"ALERT: {SENSOR_LOCATION_NAME} temperature below {SENSOR_LOWER_THRESHOLD_TEMP}°F")
                    SENSOR_BELOW_ALERT_SENT = True

            else:
                SENSOR_ABOVE_THRESHOLD_COUNT = 0
                SENSOR_ALERT_SENT = False
                SENSOR_BELOW_THRESHOLD_COUNT = 0
                SENSOR_BELOW_ALERT_SENT = False

            if co2 > SENSOR_CO2_THRESHOLD:
                SENSOR_CO2_ABOVE_THRESHOLD_COUNT += 1
                if SENSOR_CO2_ABOVE_THRESHOLD_COUNT >= THRESHOLD_COUNT and not SENSOR_CO2_ALERT_SENT:
                    slack_client.chat_postMessage(channel=SLACK_CHANNEL, text=f"ALERT: {SENSOR_LOCATION_NAME} CO2 above {SENSOR_CO2_THRESHOLD} ppm")
                    SENSOR_CO2_ALERT_SENT = True
            else:
                SENSOR_CO2_ABOVE_THRESHOLD_COUNT = 0
                SENSOR_CO2_ALERT_SENT = False

        time.sleep(MINUTES_BETWEEN_READS * 60)

# Start the monitoring in a separate thread
monitoring_thread = Thread(target=run_monitoring)
monitoring_thread.start()

# Run the Flask web application
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

# Run the Flask web application
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
