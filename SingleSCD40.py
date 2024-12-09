# Import necessary libraries and modules
from flask import Flask, request, render_template, redirect, jsonify
import time
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import configparser
from Adafruit_IO import Client, RequestError, Feed
from threading import Thread, Event
import os
import busio
import adafruit_scd4x
import socket
import sys
import logging
import traceback
import signal
from logging.handlers import RotatingFileHandler

# Initialize the Flask web application
app = Flask(__name__)

# Set up logging with RotatingFileHandler
handler = RotatingFileHandler('app.log', maxBytes=10 * 1024 * 1024, backupCount=5)
handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler())  # Also log to console

# Define locations for log files
LOG_FILE = "sensor_readings.log"
ERROR_LOG_FILE = "error_log.log"

# Global event for graceful shutdown
shutdown_event = Event()

# Global variable for Adafruit IO client
adafruit_io_client = None

# Global state tracking for alerts
alert_states = {
    'high_temp': False,
    'low_temp': False,
    'high_co2': False
}

def send_slack_alert(message):
    """Send alert to Slack channel"""
    try:
        settings = read_settings_from_conf('SingleSensorSettings.conf')
        slack_client = WebClient(token=settings['SLACK_API_TOKEN'])
        response = slack_client.chat_postMessage(
            channel=settings['SLACK_CHANNEL'],
            text=message
        )
        logger.info(f"Slack alert sent: {message}")
        return True
    except SlackApiError as e:
        log_error(f"Failed to send Slack alert: {str(e)}")
        return False
    except Exception as e:
        log_error(f"Error sending Slack alert: {str(e)}")
        return False


def celsius_to_fahrenheit(celsius):
    """Convert Celsius to Fahrenheit"""
    return (celsius * 9/5) + 32


def find_available_port(start_port=5000, max_attempts=100):
    """Find an available port starting from start_port"""
    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('', port))
                return port
        except OSError:
            continue
    raise RuntimeError("No available ports found")


def read_settings_from_conf(conf_file):
    """Read and validate settings from configuration file"""
    config = configparser.ConfigParser()
    config.read(conf_file)
    settings = {}
    keys = [
        'SENSOR_LOCATION_NAME', 'MINUTES_BETWEEN_READS', 'SENSOR_THRESHOLD_TEMP',
        'SENSOR_LOWER_THRESHOLD_TEMP', 'THRESHOLD_COUNT', 'SLACK_API_TOKEN',
        'SLACK_CHANNEL', 'ADAFRUIT_IO_USERNAME', 'ADAFRUIT_IO_KEY',
        'ADAFRUIT_IO_GROUP_NAME', 'ADAFRUIT_IO_TEMP_FEED',
        'ADAFRUIT_IO_HUMIDITY_FEED', 'ADAFRUIT_IO_CO2_FEED',
        'SENSOR_CO2_THRESHOLD'
    ]

    try:
        for key in keys:
            if key in ['SENSOR_THRESHOLD_TEMP', 'SENSOR_LOWER_THRESHOLD_TEMP', 'SENSOR_CO2_THRESHOLD']:
                settings[key] = config.getfloat('General', key)
            elif key in ['MINUTES_BETWEEN_READS', 'THRESHOLD_COUNT']:
                settings[key] = config.getint('General', key)
            else:
                settings[key] = config.get('General', key)
    except configparser.NoOptionError as e:
        log_error(f"Missing {key} in configuration file.")
        raise ValueError(f"Missing {key} in configuration file.") from e
    except Exception as e:
        log_error(f"Error reading configuration: {str(e)}")
        raise ValueError(f"Error reading configuration: {str(e)}") from e

    return settings


def log_error(message):
    """Log error messages to file and console"""
    with open(ERROR_LOG_FILE, 'a') as file:
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        file.write(f"{timestamp} - ERROR: {message}\n")
    logger.error(message)


def send_to_adafruit(feed_key, value, group_name='castle-sensors'):
    """Send data to Adafruit IO feed within a group"""
    global adafruit_io_client

    if not adafruit_io_client:
        logger.error("Adafruit IO client is not initialized.")
        return False

    try:
        # Format value to handle different types
        if isinstance(value, (int, float)):
            formatted_value = f"{value:.2f}"
        else:
            formatted_value = str(value)

        # Format the feed key with group name
        full_feed_key = f"{group_name}.{feed_key}"

        logger.debug(f"Sending to Adafruit IO - Feed: {full_feed_key}, Value: {formatted_value}")

        # Retry logic
        max_retries = 3
        retry_delay = 2

        for attempt in range(max_retries):
            try:
                # Send to feed using group.feed format
                response = adafruit_io_client.send_data(full_feed_key, formatted_value)
                logger.debug(f"Successfully sent to Adafruit IO - Feed: {full_feed_key}, Value: {formatted_value}")
                return True
            except RequestError as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Retry {attempt + 1}/{max_retries} for feed '{full_feed_key}' after error: {str(e)}")
                    time.sleep(retry_delay)
                else:
                    raise

    except RequestError as e:
        log_error(f"Adafruit IO RequestError for feed '{full_feed_key}': {str(e)}")
        return False
    except Exception as e:
        log_error(f"Error sending data to Adafruit IO feed '{full_feed_key}': {str(e)}\n{traceback.format_exc()}")
        return False


@app.route('/')
def home():
    """Home page redirect to settings"""
    return redirect('/settings')


@app.route('/settings', methods=['GET', 'POST'])
def settings_route():
    """Handle settings page and form submission"""
    conf_file = 'SingleSensorSettings.conf'
    if request.method == 'POST':
        try:
            action = request.form.get('action')
            new_settings = {}

            # Get current settings to determine types
            current_settings = read_settings_from_conf(conf_file)

            # Process each setting with proper type conversion
            for key, value in request.form.items():
                if key == 'action':
                    continue
                try:
                    if isinstance(current_settings[key], float):
                        new_settings[key] = float(value)
                    elif isinstance(current_settings[key], int):
                        new_settings[key] = int(value)
                    else:
                        new_settings[key] = value.strip()  # Sanitize string inputs
                except ValueError:
                    return jsonify(error=f'Invalid value for {key}. Expected {type(current_settings[key]).__name__}'), 400

            # Write the new settings
            config = configparser.ConfigParser()
            config['General'] = {str(k): str(v) for k, v in new_settings.items()}
            with open(conf_file, 'w') as configfile:
                config.write(configfile)

            # Handle reboot action
            if action == "reboot":
                return reboot_system()

            return jsonify(message='Settings updated successfully!'), 200

        except Exception as e:
            log_error(f"Settings update error: {e}")
            return jsonify(error=f'Error updating settings: {str(e)}'), 500
    else:
        try:
            current_settings = read_settings_from_conf(conf_file)
            return render_template('settings.html', settings=current_settings)
        except Exception as e:
            log_error(f"Error loading settings: {str(e)}")
            return jsonify(error=f'Error loading settings: {str(e)}'), 500


def reboot_system():
    """Reboot the system securely using subprocess"""
    try:
        logger.info("System reboot requested")
        os.system('sudo shutdown -r now')
        return jsonify(message='System is rebooting...'), 200
    except Exception as e:
        log_error(f"Reboot failed: {e}")
        return jsonify(error='Error: Failed to reboot system'), 500


def run_monitoring():
    """Main monitoring function"""
    global adafruit_io_client, alert_states

    # Read settings
    try:
        settings = read_settings_from_conf('SingleSensorSettings.conf')

        # Initialize Adafruit IO client
        adafruit_io_client = Client(settings['ADAFRUIT_IO_USERNAME'],
                                  settings['ADAFRUIT_IO_KEY'])
        group_name = settings['ADAFRUIT_IO_GROUP_NAME']  # Should be 'castle-sensors'
        logger.info(f"Adafruit IO client initialized successfully for group {group_name}")
    except Exception as e:
        log_error(f"Failed to initialize settings or Adafruit IO client: {e}")
        sys.exit(1)

    try:
        # Initialize I2C with corrected pins
        i2c = busio.I2C(3, 2)  # Replace with appropriate I2C pins
        sensor = adafruit_scd4x.SCD4X(i2c)
        sensor.start_periodic_measurement()
        logger.info("SCD4X sensor initialized successfully")
        time.sleep(2)  # Give sensor time to start up
    except Exception as e:
        log_error(f"Failed to initialize sensor: {e}")
        sys.exit(1)

    minutes_between_reads = settings['MINUTES_BETWEEN_READS']
    last_read_time = 0

    while not shutdown_event.is_set():
        try:
            current_time = time.time()
            if current_time - last_read_time >= (minutes_between_reads * 60):
                if sensor.data_ready:
                    # Read sensor data and convert temperature to Fahrenheit
                    temperature_c = sensor.temperature
                    temperature_f = celsius_to_fahrenheit(temperature_c)
                    humidity = sensor.relative_humidity
                    co2 = sensor.CO2

                    logger.info(f"Read values - Temp: {temperature_f}°F ({temperature_c}°C), Humidity: {humidity}%, CO2: {co2}ppm")

                    # Check high temperature threshold
                    is_temp_high = temperature_f >= settings['SENSOR_THRESHOLD_TEMP']
                    if is_temp_high != alert_states['high_temp']:
                        alert_states['high_temp'] = is_temp_high
                        if is_temp_high:
                            alert_msg = (f"🔥 High temperature alert at {settings['SENSOR_LOCATION_NAME']}: "
                                       f"{temperature_f:.1f}°F ({temperature_c:.1f}°C)")
                        else:
                            alert_msg = (f"✅ Temperature returned to normal at {settings['SENSOR_LOCATION_NAME']}: "
                                       f"{temperature_f:.1f}°F ({temperature_c:.1f}°C)")
                        send_slack_alert(alert_msg)

                    # Check low temperature threshold
                    is_temp_low = temperature_f <= settings['SENSOR_LOWER_THRESHOLD_TEMP']
                    if is_temp_low != alert_states['low_temp']:
                        alert_states['low_temp'] = is_temp_low
                        if is_temp_low:
                            alert_msg = (f"❄️ Low temperature alert at {settings['SENSOR_LOCATION_NAME']}: "
                                       f"{temperature_f:.1f}°F ({temperature_c:.1f}°C)")
                        else:
                            alert_msg = (f"✅ Temperature returned to normal at {settings['SENSOR_LOCATION_NAME']}: "
                                       f"{temperature_f:.1f}°F ({temperature_c:.1f}°C)")
                        send_slack_alert(alert_msg)

                    # Check CO2 threshold
                    is_co2_high = co2 >= settings['SENSOR_CO2_THRESHOLD']
                    if is_co2_high != alert_states['high_co2']:
                        alert_states['high_co2'] = is_co2_high
                        if is_co2_high:
                            alert_msg = (f"⚠️ High CO2 alert at {settings['SENSOR_LOCATION_NAME']}: {co2}ppm")
                        else:
                            alert_msg = (f"✅ CO2 returned to normal at {settings['SENSOR_LOCATION_NAME']}: {co2}ppm")
                        send_slack_alert(alert_msg)

                    # Send to Adafruit IO with group name (using Fahrenheit)
                    send_to_adafruit(settings['ADAFRUIT_IO_TEMP_FEED'], temperature_f, settings['ADAFRUIT_IO_GROUP_NAME'])
                    send_to_adafruit(settings['ADAFRUIT_IO_HUMIDITY_FEED'], humidity, settings['ADAFRUIT_IO_GROUP_NAME'])
                    send_to_adafruit(settings['ADAFRUIT_IO_CO2_FEED'], co2, settings['ADAFRUIT_IO_GROUP_NAME'])

                    last_read_time = current_time

            time.sleep(5)  # Short sleep to prevent CPU overuse
        except Exception as e:
            log_error(f"Error in monitoring loop: {e}")
            time.sleep(5)


def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info("Shutdown signal received. Cleaning up...")
    shutdown_event.set()


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        monitoring_thread = Thread(target=run_monitoring)
        monitoring_thread.start()

        port = find_available_port(5000)
        logger.info(f"Starting Flask app on port {port}...")
        app.run(host='0.0.0.0', port=port, debug=False)
    except Exception as e:
        log_error(f"Error starting server: {e}")
    finally:
        shutdown_event.set()
        monitoring_thread.join()
