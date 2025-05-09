#!/usr/bin/env python3
"""
Docking Station Management Panel
A cross-platform desktop application that monitors USB device events, system temperatures, 
and battery status with real-time visualization and historical reporting.
"""

import sys
import os
import time
import sqlite3
import json
import csv
import datetime
from threading import Thread, Event
import platform
import psutil
import pythoncom



# GUI imports
from PyQt5.QtWidgets import (QApplication, QMainWindow, QTabWidget, QVBoxLayout, QHBoxLayout, 
                            QWidget, QPushButton, QLabel, QDateEdit, QTableWidget, 
                            QTableWidgetItem, QFileDialog, QProgressBar, QFrame, 
                            QGridLayout, QSplitter, QMessageBox)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QDate, QThread
from PyQt5.QtGui import QFont, QColor

# For charts
import matplotlib
matplotlib.use('Qt5Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import numpy as np

# Platform-specific imports for USB monitoring
SYSTEM = platform.system()
if SYSTEM == "Windows":
    import win32api
    import win32con
    import win32gui
    import wmi
elif SYSTEM == "Linux":
    try:
        import pyudev
    except ImportError:
        print("pyudev not installed. USB monitoring on Linux will be simulated.")

# Try to import GPU monitoring
try:
    import GPUtil
    HAS_GPU = True
except ImportError:
    print("GPUtil not installed. GPU monitoring will be disabled.")
    HAS_GPU = False

# Constants
DB_PATH = "dockingstation.db"
POLLING_INTERVAL = 5  # seconds
NOTIFICATION_DURATION = 5000  # milliseconds

class Database:
    """Database handler for the application"""
    
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self.create_schema()
    
    def get_connection(self):
        """Get a database connection"""
        conn = sqlite3.connect(self.db_path)
        return conn
    
    def create_schema(self):
        """Create the database schema if it doesn't exist"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Create USB events table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS usb_events (
            id INTEGER PRIMARY KEY,
            timestamp DATETIME,
            event_type TEXT,
            vendor TEXT,
            serial TEXT,
            uuid TEXT
        )
        ''')
        
        # Create hardware stats table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS hw_stats (
            id INTEGER PRIMARY KEY,
            timestamp DATETIME,
            cpu_temp REAL,
            gpu_temp REAL,
            battery_level INTEGER,
            battery_health TEXT
        )
        ''')
        
        conn.commit()
        conn.close()
    
    def add_usb_event(self, event_type, vendor, serial, uuid):
        """Add a USB event to the database"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        timestamp = datetime.datetime.now().isoformat()
        cursor.execute(
            "INSERT INTO usb_events (timestamp, event_type, vendor, serial, uuid) VALUES (?, ?, ?, ?, ?)",
            (timestamp, event_type, vendor, serial, uuid)
        )
        
        conn.commit()
        conn.close()
        return cursor.lastrowid
    
    def add_hw_stats(self, cpu_temp, gpu_temp, battery_level, battery_health):
        """Add hardware stats to the database"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        timestamp = datetime.datetime.now().isoformat()
        cursor.execute(
            "INSERT INTO hw_stats (timestamp, cpu_temp, gpu_temp, battery_level, battery_health) VALUES (?, ?, ?, ?, ?)",
            (timestamp, cpu_temp, gpu_temp, battery_level, battery_health)
        )
        
        conn.commit()
        conn.close()
        return cursor.lastrowid
    
    def get_usb_events(self, start_date=None, end_date=None):
        """Get USB events filtered by date range"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        query = "SELECT * FROM usb_events"
        params = []
        
        if start_date or end_date:
            query += " WHERE "
            if start_date:
                query += "timestamp >= ?"
                params.append(start_date.isoformat())
                if end_date:
                    query += " AND "
            if end_date:
                query += "timestamp <= ?"
                end_datetime = datetime.datetime.combine(end_date, datetime.time.max)
                params.append(end_datetime.isoformat())
        
        query += " ORDER BY timestamp DESC"
        
        cursor.execute(query, params)
        results = cursor.fetchall()
        conn.close()
        
        return results
    
    def get_hw_stats(self, start_date=None, end_date=None):
        """Get hardware stats filtered by date range"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        query = "SELECT * FROM hw_stats"
        params = []
        
        if start_date or end_date:
            query += " WHERE "
            if start_date:
                query += "timestamp >= ?"
                params.append(start_date.isoformat())
                if end_date:
                    query += " AND "
            if end_date:
                query += "timestamp <= ?"
                end_datetime = datetime.datetime.combine(end_date, datetime.time.max)
                params.append(end_datetime.isoformat())
        
        query += " ORDER BY timestamp ASC"
        
        cursor.execute(query, params)
        results = cursor.fetchall()
        conn.close()
        
        return results

    def get_latest_usb_events(self, limit=5):
        """Get the most recent USB events"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM usb_events ORDER BY timestamp DESC LIMIT ?", (limit,))
        results = cursor.fetchall()
        conn.close()
        
        return results
    
    def get_latest_hw_stats(self):
        """Get the most recent hardware stats"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM hw_stats ORDER BY timestamp DESC LIMIT 1")
        result = cursor.fetchone()
        conn.close()
        
        return result

class USBMonitor(QObject):
    """Monitor USB devices and detect plug/unplug events"""
    
    # Signal emitted when a USB event is detected
    usb_event = pyqtSignal(str, str, str, str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.stop_event = Event()
        self.known_devices = set()  # Track known devices
        
        # Initialize platform-specific monitoring
        if SYSTEM == "Windows":
            self.wmi = wmi.WMI()
            self.initialize_windows()
        elif SYSTEM == "Linux":
            self.initialize_linux()
        elif SYSTEM == "Darwin":
            self.initialize_macos()
        
        # Initial device scan
        self.scan_devices()
    
    def initialize_windows(self):
        """Initialize Windows-specific USB monitoring"""
        self.windows_monitor_thread = None
    
    def initialize_linux(self):
        """Initialize Linux-specific USB monitoring"""
        if 'pyudev' in sys.modules:
            self.context = pyudev.Context()
            self.monitor = pyudev.Monitor.from_netlink(self.context)
            self.monitor.filter_by(subsystem='usb')
        else:
            # Simulation mode
            pass
    
    def initialize_macos(self):
        """Initialize macOS-specific USB monitoring"""
        # macOS implementation would go here
        pass
    
    def scan_devices(self):
        """Scan for currently connected USB devices"""
        current_devices = set()
        
        if SYSTEM == "Windows":
            for device in self.wmi.Win32_USBControllerDevice():
                try:
                    dependent = device.Dependent
                    if dependent:
                        device_id = dependent.DeviceID
                        vendor = "Unknown"
                        if "VID_" in device_id:
                            vendor = device_id.split("VID_")[1].split("&")[0]
                        current_devices.add((device_id, vendor, "Unknown", "Unknown"))
                except Exception as e:
                    print(f"Error scanning Windows USB device: {e}")
        
        elif SYSTEM == "Linux" and 'pyudev' in sys.modules:
            for device in self.context.list_devices(subsystem='usb', DEVTYPE='usb_device'):
                try:
                    device_id = device.get('DEVPATH', 'Unknown')
                    vendor = device.get('ID_VENDOR', 'Unknown')
                    serial = device.get('ID_SERIAL', 'Unknown')
                    uuid = device.get('ID_UUID', 'Unknown')
                    current_devices.add((device_id, vendor, serial, uuid))
                except Exception as e:
                    print(f"Error scanning Linux USB device: {e}")
        
        # Find new devices
        for device in current_devices - self.known_devices:
            _, vendor, serial, uuid = device
            self.usb_event.emit("add", vendor, serial, uuid)
        
        # Update known devices
        self.known_devices = current_devices
    
    def start_monitoring(self):
        """Start monitoring for USB events"""
        if SYSTEM == "Windows":
            self.windows_monitor_thread = Thread(target=self._windows_monitor_worker)
            self.windows_monitor_thread.daemon = True
            self.windows_monitor_thread.start()
        
        elif SYSTEM == "Linux" and 'pyudev' in sys.modules:
            self.linux_monitor_thread = Thread(target=self._linux_monitor_worker)
            self.linux_monitor_thread.daemon = True
            self.linux_monitor_thread.start()
        
        elif SYSTEM == "Darwin":
            # macOS monitoring implementation would go here
            self.macos_monitor_thread = Thread(target=self._macos_monitor_worker)
            self.macos_monitor_thread.daemon = True
            self.macos_monitor_thread.start()
    
    def _windows_monitor_worker(self):
        pythoncom.CoInitialize() 
        """Worker thread for Windows USB monitoring"""
        while not self.stop_event.is_set():
            try:
                # Scan for changes in USB devices
                previous_devices = self.known_devices.copy()
                self.scan_devices()
                
                # Detect removed devices
                for device in previous_devices - self.known_devices:
                    _, vendor, serial, uuid = device
                    self.usb_event.emit("remove", vendor, serial, uuid)
                
                time.sleep(1)  # Check every second
            except Exception as e:
                print(f"Error in Windows USB monitoring: {e}")
                time.sleep(5)  # Back off on error
    
    def _linux_monitor_worker(self):
        """Worker thread for Linux USB monitoring"""
        if 'pyudev' in sys.modules:
            try:
                self.monitor.start()
                for device in iter(self.monitor.poll, None):
                    if self.stop_event.is_set():
                        break
                    
                    try:
                        device_id = device.get('DEVPATH', 'Unknown')
                        vendor = device.get('ID_VENDOR', 'Unknown')
                        serial = device.get('ID_SERIAL', 'Unknown')
                        uuid = device.get('ID_UUID', 'Unknown')
                        
                        if device.action == 'add':
                            self.known_devices.add((device_id, vendor, serial, uuid))
                            self.usb_event.emit("add", vendor, serial, uuid)
                        elif device.action == 'remove':
                            self.known_devices.discard((device_id, vendor, serial, uuid))
                            self.usb_event.emit("remove", vendor, serial, uuid)
                    except Exception as e:
                        print(f"Error processing Linux USB event: {e}")
            except Exception as e:
                print(f"Error in Linux USB monitoring: {e}")
        else:
            # Simulation mode
            while not self.stop_event.is_set():
                self.scan_devices()
                time.sleep(2)
    
    def _macos_monitor_worker(self):
        """Worker thread for macOS USB monitoring"""
        # Simplified simulation mode for macOS
        while not self.stop_event.is_set():
            try:
                previous_devices = self.known_devices.copy()
                self.scan_devices()
                
                # Detect removed devices
                for device in previous_devices - self.known_devices:
                    _, vendor, serial, uuid = device
                    self.usb_event.emit("remove", vendor, serial, uuid)
                
                time.sleep(2)
            except Exception as e:
                print(f"Error in macOS USB monitoring: {e}")
                time.sleep(5)
    
    def stop_monitoring(self):
        """Stop USB monitoring"""
        self.stop_event.set()
        
        if SYSTEM == "Windows" and self.windows_monitor_thread:
            self.windows_monitor_thread.join(timeout=1.0)
        
        elif SYSTEM == "Linux" and 'pyudev' in sys.modules:
            if hasattr(self, 'linux_monitor_thread'):
                self.linux_monitor_thread.join(timeout=1.0)
        
        elif SYSTEM == "Darwin":
            if hasattr(self, 'macos_monitor_thread'):
                self.macos_monitor_thread.join(timeout=1.0)

class HardwareSensorPoller(QObject):
    """Poll hardware sensors for system information"""
    
    # Signal emitted when new sensor data is available
    sensor_data = pyqtSignal(float, float, int, str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.stop_event = Event()
        self.polling_interval = POLLING_INTERVAL
    
    def start_polling(self):
        """Start polling hardware sensors"""
        self.polling_thread = Thread(target=self._polling_worker)
        self.polling_thread.daemon = True
        self.polling_thread.start()
    
    def _polling_worker(self):
        pythoncom.CoInitialize() 
        """Worker thread for sensor polling"""
        while not self.stop_event.is_set():
            try:
                # Get CPU temperature
                cpu_temp = self._get_cpu_temp()
                
                # Get GPU temperature
                gpu_temp = self._get_gpu_temp()
                
                # Get battery information
                battery_level, battery_health = self._get_battery_info()
                
                # Emit the sensor data
                self.sensor_data.emit(cpu_temp, gpu_temp, battery_level, battery_health)
                
                # Sleep until next poll
                time.sleep(self.polling_interval)
            
            except Exception as e:
                print(f"Error polling hardware sensors: {e}")
                time.sleep(self.polling_interval * 2)  # Back off on error
    
    def _get_cpu_temp(self):
        """Get CPU temperature"""
        try:
            if SYSTEM == "Windows":
                # Windows CPU temp via WMI (simplified)
                import wmi
                w = wmi.WMI(namespace="root\\wmi")
                temperature_info = w.MSAcpi_ThermalZoneTemperature()[0]
                return float(temperature_info.CurrentTemperature / 10.0 - 273.15)
            
            elif SYSTEM == "Linux":
                # Try to read from system files
                for thermal_zone in range(10):  # Check first 10 thermal zones
                    path = f"/sys/class/thermal/thermal_zone{thermal_zone}/temp"
                    if os.path.exists(path):
                        with open(path, 'r') as f:
                            temp = int(f.read().strip()) / 1000.0
                            if temp > 0:  # Valid temperature
                                return temp
                
                # If not found, use psutil as fallback
                temps = psutil.sensors_temperatures()
                if temps:
                    for name, entries in temps.items():
                        if entries:
                            return entries[0].current
            
            elif SYSTEM == "Darwin":
                # macOS CPU temp (simplified simulation)
                import subprocess
                result = subprocess.run(['sysctl', '-n', 'machdep.xcpm.cpu_thermal_level'], 
                                       capture_output=True, text=True)
                thermal_level = int(result.stdout.strip())
                # Map thermal level to temperature (rough estimate)
                base_temp = 45.0
                return base_temp + (thermal_level * 5.0)
            
            # Fallback: simulated temperature
            return 50.0 + (5.0 * np.random.random())
        
        except Exception as e:
            print(f"Error getting CPU temperature: {e}")
            return 50.0  # Default fallback value
    
    def _get_gpu_temp(self):
        """Get GPU temperature"""
        try:
            if not HAS_GPU:
                return 0.0
            
            gpus = GPUtil.getGPUs()
            if gpus:
                return gpus[0].temperature
            return 0.0
        
        except Exception as e:
            print(f"Error getting GPU temperature: {e}")
            return 0.0
    
    def _get_battery_info(self):
        """Get battery level and health"""
        try:
            battery = psutil.sensors_battery()
            if battery:
                level = int(battery.percent)
                
                # Determine battery health based on percentage
                if level >= 80:
                    health = "Good"
                elif level >= 50:
                    health = "Fair"
                elif level >= 20:
                    health = "Poor"
                else:
                    health = "Critical"
                
                return level, health
            
            return 0, "No Battery"
        
        except Exception as e:
            print(f"Error getting battery info: {e}")
            return 0, "Unknown"
    
    def stop_polling(self):
        """Stop polling hardware sensors"""
        self.stop_event.set()
        if hasattr(self, 'polling_thread'):
            self.polling_thread.join(timeout=1.0)

class DashboardWidget(QWidget):
    """Widget for displaying real-time system information"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.initUI()
    
    def initUI(self):
        """Initialize the dashboard UI"""
        layout = QVBoxLayout()
        
        # Add a title
        title_label = QLabel("System Dashboard")
        title_label.setAlignment(Qt.AlignCenter)
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        layout.addWidget(title_label)
        
        # Add hardware monitoring section
        hw_frame = QFrame()
        hw_frame.setFrameShape(QFrame.StyledPanel)
        hw_layout = QHBoxLayout()
        
        # CPU Temperature
        cpu_layout = QVBoxLayout()
        self.cpu_temp_label = QLabel("CPU Temperature")
        self.cpu_temp_label.setAlignment(Qt.AlignCenter)
        self.cpu_temp_value = QLabel("0.0°C")
        self.cpu_temp_value.setAlignment(Qt.AlignCenter)
        cpu_font = QFont()
        cpu_font.setPointSize(14)
        self.cpu_temp_value.setFont(cpu_font)
        self.cpu_progress = QProgressBar()
        self.cpu_progress.setRange(0, 100)
        self.cpu_progress.setValue(0)
        cpu_layout.addWidget(self.cpu_temp_label)
        cpu_layout.addWidget(self.cpu_temp_value)
        cpu_layout.addWidget(self.cpu_progress)
        hw_layout.addLayout(cpu_layout)
        
        # GPU Temperature
        gpu_layout = QVBoxLayout()
        self.gpu_temp_label = QLabel("GPU Temperature")
        self.gpu_temp_label.setAlignment(Qt.AlignCenter)
        self.gpu_temp_value = QLabel("0.0°C")
        self.gpu_temp_value.setAlignment(Qt.AlignCenter)
        self.gpu_temp_value.setFont(cpu_font)  # Reuse the same font
        self.gpu_progress = QProgressBar()
        self.gpu_progress.setRange(0, 100)
        self.gpu_progress.setValue(0)
        gpu_layout.addWidget(self.gpu_temp_label)
        gpu_layout.addWidget(self.gpu_temp_value)
        gpu_layout.addWidget(self.gpu_progress)
        hw_layout.addLayout(gpu_layout)
        
        # Battery Status
        battery_layout = QVBoxLayout()
        self.battery_label = QLabel("Battery Level")
        self.battery_label.setAlignment(Qt.AlignCenter)
        self.battery_value = QLabel("0%")
        self.battery_value.setAlignment(Qt.AlignCenter)
        self.battery_value.setFont(cpu_font)  # Reuse the same font
        self.battery_progress = QProgressBar()
        self.battery_progress.setRange(0, 100)
        self.battery_progress.setValue(0)
        self.battery_health = QLabel("Health: Unknown")
        self.battery_health.setAlignment(Qt.AlignCenter)
        battery_layout.addWidget(self.battery_label)
        battery_layout.addWidget(self.battery_value)
        battery_layout.addWidget(self.battery_progress)
        battery_layout.addWidget(self.battery_health)
        hw_layout.addLayout(battery_layout)
        
        hw_frame.setLayout(hw_layout)
        layout.addWidget(hw_frame)
        
        # Add charts section
        charts_frame = QFrame()
        charts_frame.setFrameShape(QFrame.StyledPanel)
        charts_layout = QVBoxLayout()
        
        # Create the temperature chart
        self.figure = Figure(figsize=(6, 4), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        self.ax = self.figure.add_subplot(111)
        self.ax.set_title("Temperature History")
        self.ax.set_xlabel("Time")
        self.ax.set_ylabel("Temperature (°C)")
        charts_layout.addWidget(self.canvas)
        
        charts_frame.setLayout(charts_layout)
        layout.addWidget(charts_frame)
        
        # USB Events section
        usb_frame = QFrame()
        usb_frame.setFrameShape(QFrame.StyledPanel)
        usb_layout = QVBoxLayout()
        
        usb_title = QLabel("Recent USB Events")
        usb_title.setAlignment(Qt.AlignCenter)
        usb_title_font = QFont()
        usb_title_font.setPointSize(14)
        usb_title_font.setBold(True)
        usb_title.setFont(usb_title_font)
        usb_layout.addWidget(usb_title)
        
        # Create USB events table
        self.usb_table = QTableWidget(5, 5)  # 5 rows, 5 columns
        self.usb_table.setHorizontalHeaderLabels(["Time", "Type", "Vendor", "Serial", "UUID"])
        self.usb_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.usb_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.usb_table.horizontalHeader().setStretchLastSection(True)
        usb_layout.addWidget(self.usb_table)
        
        usb_frame.setLayout(usb_layout)
        layout.addWidget(usb_frame)
        
        self.setLayout(layout)
        
        # Initialize data
        self.timestamps = []
        self.cpu_temps = []
        self.gpu_temps = []
    
    def update_sensor_data(self, cpu_temp, gpu_temp, battery_level, battery_health):
        """Update the dashboard with new sensor data"""
        # Update CPU temperature
        self.cpu_temp_value.setText(f"{cpu_temp:.1f}°C")
        self.cpu_progress.setValue(min(100, int(cpu_temp)))
        if cpu_temp < 50:
            self.cpu_progress.setStyleSheet("QProgressBar::chunk { background-color: green; }")
        elif cpu_temp < 70:
            self.cpu_progress.setStyleSheet("QProgressBar::chunk { background-color: yellow; }")
        else:
            self.cpu_progress.setStyleSheet("QProgressBar::chunk { background-color: red; }")
        
        # Update GPU temperature
        if gpu_temp > 0:
            self.gpu_temp_value.setText(f"{gpu_temp:.1f}°C")
            self.gpu_progress.setValue(min(100, int(gpu_temp)))
            if gpu_temp < 60:
                self.gpu_progress.setStyleSheet("QProgressBar::chunk { background-color: green; }")
            elif gpu_temp < 80:
                self.gpu_progress.setStyleSheet("QProgressBar::chunk { background-color: yellow; }")
            else:
                self.gpu_progress.setStyleSheet("QProgressBar::chunk { background-color: red; }")
        else:
            self.gpu_temp_value.setText("N/A")
            self.gpu_progress.setValue(0)
        
        # Update battery status
        self.battery_value.setText(f"{battery_level}%")
        self.battery_progress.setValue(battery_level)
        self.battery_health.setText(f"Health: {battery_health}")
        if battery_health == "Good":
            self.battery_progress.setStyleSheet("QProgressBar::chunk { background-color: green; }")
        elif battery_health == "Fair":
            self.battery_progress.setStyleSheet("QProgressBar::chunk { background-color: yellow; }")
        elif battery_health == "Poor" or battery_health == "Critical":
            self.battery_progress.setStyleSheet("QProgressBar::chunk { background-color: red; }")
        
        # Update chart data
        current_time = datetime.datetime.now().strftime("%H:%M:%S")
        self.timestamps.append(current_time)
        self.cpu_temps.append(cpu_temp)
        self.gpu_temps.append(gpu_temp if gpu_temp > 0 else None)
        
        # Keep only the last 20 data points
        if len(self.timestamps) > 20:
            self.timestamps.pop(0)
            self.cpu_temps.pop(0)
            self.gpu_temps.pop(0)
        
        # Update the chart
        self.update_chart()
    
    def update_chart(self):
        """Update the temperature chart"""
        self.ax.clear()
        self.ax.set_title("Temperature History")
        self.ax.set_xlabel("Time")
        self.ax.set_ylabel("Temperature (°C)")
        
        # Plot CPU temperature
        self.ax.plot(self.timestamps, self.cpu_temps, 'b-', label="CPU")
        
        # Plot GPU temperature if available
        if any(temp is not None for temp in self.gpu_temps):
            self.ax.plot(self.timestamps, self.gpu_temps, 'r-', label="GPU")
        
        # X-axis formatting
        if len(self.timestamps) > 10:
            # Show fewer x-axis labels to prevent crowding
            ticks_pos = list(range(0, len(self.timestamps), len(self.timestamps) // 5))
            ticks_pos.append(len(self.timestamps) - 1)  # Add the last position
            self.ax.set_xticks(ticks_pos)
            self.ax.set_xticklabels([self.timestamps[i] for i in ticks_pos])
        else:
            self.ax.set_xticks(range(len(self.timestamps)))
            self.ax.set_xticklabels(self.timestamps)
        
        # Rotate x-axis labels for better readability
        plt.setp(self.ax.get_xticklabels(), rotation=45, ha='right')
        
        self.ax.legend()
        self.ax.grid(True)
        
        # Make sure the chart stays within reasonable y-axis limits
        self.ax.set_ylim(
            min(10, min(min(self.cpu_temps), min(t for t in self.gpu_temps if t is not None) if any(t is not None for t in self.gpu_temps) else 0) - 5),
            max(100, max(max(self.cpu_temps), max(t for t in self.gpu_temps if t is not None) if any(t is not None for t in self.gpu_temps) else 0) + 5)
        )
        
        # Refresh the canvas
        self.figure.tight_layout()
        self.canvas.draw()
    
    def update_usb_events(self, events):
        """Update the USB events table"""
        self.usb_table.clearContents()
        
        for row, event in enumerate(events[:5]):  # Show up to 5 events
            # Parse the timestamp
            timestamp = datetime.datetime.fromisoformat(event[1])
            formatted_time = timestamp.strftime("%H:%M:%S")
            
            # Add event details to the table
            self.usb_table.setItem(row, 0, QTableWidgetItem(formatted_time))
            self.usb_table.setItem(row, 1, QTableWidgetItem(event[2]))  # event_type
            self.usb_table.setItem(row, 2, QTableWidgetItem(event[3]))  # vendor
            self.usb_table.setItem(row, 3, QTableWidgetItem(event[4]))  # serial
            self.usb_table.setItem(row, 4, QTableWidgetItem(event[5]))  # uuid
            
            # Add color to the row based on the event type
            if event[2] == "add":
                for col in range(5):
                    self.usb_table.item(row, col).setBackground(QColor(200, 255, 200))  # Light green
            else:  # "remove"
                for col in range(5):
                    self.usb_table.item
                    self.usb_table.item(row, col).setBackground(QColor(255, 200, 200))  # Light red

def main():
    """Main function to run the application"""
    app = QApplication(sys.argv)
    
    # Create database and dashboard
    db = Database()
    dashboard = DashboardWidget()

    # Create USB monitor and sensor poller
    usb_monitor = USBMonitor()
    sensor_poller = HardwareSensorPoller()

    # Connect signals
    usb_monitor.usb_event.connect(lambda event_type, vendor, serial, uuid: (
        db.add_usb_event(event_type, vendor, serial, uuid),
        dashboard.update_usb_events(db.get_latest_usb_events())
    ))
    sensor_poller.sensor_data.connect(lambda cpu, gpu, batt, health: (
        db.add_hw_stats(cpu, gpu, batt, health),
        dashboard.update_sensor_data(cpu, gpu, batt, health)
    ))

    # Start background threads
    usb_monitor.start_monitoring()
    sensor_poller.start_polling()

    # Set up main window
    main_window = QMainWindow()
    main_window.setWindowTitle("Docking Station Management Panel")
    main_window.setCentralWidget(dashboard)
    main_window.resize(1000, 700)
    main_window.show()

    # Load initial USB events
    dashboard.update_usb_events(db.get_latest_usb_events())

    # Clean exit
    def on_exit():
        usb_monitor.stop_monitoring()
        sensor_poller.stop_polling()
    app.aboutToQuit.connect(on_exit)

    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
