# Import necessary libraries and modules
from flask import Flask, request, render_template  # Flask web framework
import time  # For time-related functions
from slack_sdk import WebClient  # Communicate with Slack
from slack_sdk.errors import SlackApiError  # Handle Slack errors
import configparser  # Read and write configuration files
from Adafruit_IO import Client  # Interface with Adafruit IO platform
from threading import Thread  # Run tasks in parallel threads
import os  # Interface with the OS, e.g., for rebooting
import adafruit_scd40  # Library for SCD-40 sensor

# Initialize the Flask web application
app = Flask(__name__)

# Define locations for log files
LOG_FILE = "sensor_readings.log"
ERROR_LOG_FILE = "error_log.log"

# Function to read settings from a configuration file
def read_settings_from_conf(conf_file):
    # Initialize a configuration parser
    config = configparser.ConfigParser()
    # Read the configuration file
    config.read(conf_file)
    # Dictionary to store the settings
    settings = {}
    # List of keys we expect in the configuration file
    keys = [
        'SENSOR_LOCATION_NAME', 'MINUTES_BETWEEN_READS', 'SENSOR_THRESHOLD_TEMP',
        'SENSOR_LOWER_THRESHOLD_TEMP', 'THRESHOLD_COUNT', 'SLACK_API_TOKEN',
        'SLACK_CHANNEL', 'ADAFRUIT_IO_USERNAME', 'ADAFRUIT_IO_KEY',
        'ADAFRUIT_IO_GROUP_NAME', 'ADAFRUIT_IO_TEMP_FEED', 'ADAFRUIT_IO_HUMIDITY_FEED',
        'ADAFRUIT_IO_CO2_FEED', 'SENSOR_CO2_THRESHOLD'
    ]
    # Extract each key from the configuration file
    for key in keys:
        try:
            # Fetch float values for temperature thresholds and CO2 threshold
            if key in ['SENSOR_THRESHOLD_TEMP', 'SENSOR_LOWER_THRESHOLD_TEMP', 'SENSOR_CO2_THRESHOLD']:
                settings[key] = config.getfloat('General', key)
            # Fetch integer values for read intervals and threshold counts
            elif key in ['MINUTES_BETWEEN_READS', 'THRESHOLD_COUNT']:
                settings[key] = config.getint('General', key)
            # Fetch string values for other settings
            else:
                settings[key] = config.get('General', key)
        # Handle missing keys
        except configparser.NoOptionError:
            log_error(f"Missing {key} in configuration file.")
            raise
    # Return the extracted settings
    return settings

# Function to write settings to a configuration file
def write_settings_to_conf(conf_file, settings):
    # Initialize a configuration parser
    config = configparser.ConfigParser()
    # Add the settings to the 'General' section
    config['General'] = settings
    # Write the settings to the configuration file
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

# Flask route to handle settings via a web interface
@app.route('/settings', methods=['GET', 'POST'])
def settings():
    conf_file = 'SingleSensorSettings.conf'
    if request.method == 'POST':
        # Determine the intended action (save or reboot)
        action = request.form.get('action')
        # Extract the new settings from the form
        new_settings = {key: value for key, value in request.form.items() if key != "action"}
        # Save the new settings to the configuration file
        write_settings_to_conf(conf_file, new_settings)
        # If the action is to reboot, reboot the system
        if action == "reboot":
            os.system('sudo reboot')
        return 'Settings updated!'
    else:
        # Fetch the current settings
        current_settings = read_settings_from_conf(conf_file)
        # Render the settings page with the current settings
        return render_template('settings.html', settings=current_settings)

# Function to continuously monitor sensor readings and send alerts
def run_monitoring():
    # Read initial settings
    settings = read_settings_from_conf('SingleSensorSettings.conf')
    # Store settings in global variables for easy access
    for key, value in settings.items():
        globals()[key] = value

    # Initialize counters and alert flags
    SENSOR_ABOVE_THRESHOLD_COUNT = 0
    SENSOR_ALERT_SENT = False
    SENSOR_BELOW_THRESHOLD_COUNT = 0
    SENSOR_BELOW_ALERT_SENT = False
    SENSOR_CO2_ABOVE_THRESHOLD_COUNT = 0
    SENSOR_CO2_ALERT_SENT = False

    # Initialize Slack and Adafruit IO clients
    slack_client = WebClient(token=SLACK_API_TOKEN)
    adafruit_io_client = Client(ADAFRUIT_IO_USERNAME, ADAFRUIT_IO_KEY)

    # Initialize I2C bus and SCD-40 sensor
    i2c = board.I2C()
    sensor = adafruit_scd40.SCD40(i2c)

    # Continuous monitoring loop
    while True:
        # Read temperature, humidity, and CO2 from the sensor
        temperature = sensor.temperature
        humidity = sensor.relative_humidity
        co2 = sensor.co2

        # Log the readings to a file
        log_to_file(SENSOR_LOCATION_NAME, temperature, humidity, co2)

        # Send the readings to Adafruit IO
        adafruit_io_client.send_data(f"{ADAFRUIT_IO_GROUP_NAME}.{ADAFRUIT_IO_TEMP_FEED}", temperature)
        adafruit_io_client.send_data(f"{ADAFRUIT_IO_GROUP_NAME}.{ADAFRUIT_IO_HUMIDITY_FEED}", humidity)
        adafruit_io_client.send_data(f"{ADAFRUIT_IO_GROUP_NAME}.{ADAFRUIT_IO_CO2_FEED}", co2)

        # Check temperature against thresholds and update alert flags/counters
        if temperature > SENSOR_THRESHOLD_TEMP:
            SENSOR_ABOVE_THRESHOLD_COUNT += 1
            if SENSOR_ABOVE_THRESHOLD_COUNT >= THRESHOLD_COUNT and not SENSOR_ALERT_SENT:
                # Send an alert to Slack
                slack_client.chat_postMessage(channel=SLACK_CHANNEL, text=f"ALERT: {SENSOR_LOCATION_NAME} temperature above {SENSOR_THRESHOLD_TEMP}°F")
                SENSOR_ALERT_SENT = True
        elif temperature < SENSOR_LOWER_THRESHOLD_TEMP:
            SENSOR_BELOW_THRESHOLD_COUNT += 1
            if SENSOR_BELOW_THRESHOLD_COUNT >= THRESHOLD_COUNT and not SENSOR_BELOW_ALERT_SENT:
                # Send an alert to Slack
                slack_client.chat_postMessage(channel=SLACK_CHANNEL, text=f"ALERT: {SENSOR_LOCATION_NAME} temperature below {SENSOR_LOWER_THRESHOLD_TEMP}°F")
                SENSOR_BELOW_ALERT_SENT = True
        else:
            # Reset counters and alert flags if temperature is within normal range
            SENSOR_ABOVE_THRESHOLD_COUNT = 0
            SENSOR_ALERT_SENT = False
            SENSOR_BELOW_THRESHOLD_COUNT = 0
            SENSOR_BELOW_ALERT_SENT = False

        # Check CO2 against thresholds and update alert flags/counters
        if co2 > SENSOR_CO2_THRESHOLD:
            SENSOR_CO2_ABOVE_THRESHOLD_COUNT += 1
            if SENSOR_CO2_ABOVE_THRESHOLD_COUNT >= THRESHOLD_COUNT and not SENSOR_CO2_ALERT_SENT:
                # Send an alert to Slack
                slack_client.chat_postMessage(channel=SLACK_CHANNEL, text=f"ALERT: {SENSOR_LOCATION_NAME} CO2 above {SENSOR_CO2_THRESHOLD} ppm")
                SENSOR_CO2_ALERT_SENT = True
        else:
            # Reset counters and alert flags if CO2 is within normal range
            SENSOR_CO2_ABOVE_THRESHOLD_COUNT = 0
            SENSOR_CO2_ALERT_SENT = False

        # Sleep for the specified interval before the next reading
        time.sleep(MINUTES_BETWEEN_READS * 60)

# Start the monitoring in a separate thread
monitoring_thread = Thread(target=run_monitoring)
monitoring_thread.start()

# Run the Flask web application
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
