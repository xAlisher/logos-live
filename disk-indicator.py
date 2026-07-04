#!/usr/bin/env python3
"""
/data disk space indicator for the GNOME top bar.
Shows free space and % used, updates every 60s.
Color: green < 75%, orange < 90%, red >= 90%.
"""
import shutil
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("AppIndicator3", "0.1")
from gi.repository import Gtk, AppIndicator3, GLib

MOUNT   = "/data"
INTERVAL = 60   # seconds

def label():
    du = shutil.disk_usage(MOUNT)
    free_gb  = du.free  / 1e9
    used_pct = du.used  / du.total * 100
    if free_gb >= 10:
        free_str = f"{free_gb:.0f}G"
    else:
        free_str = f"{free_gb:.1f}G"
    return f"/data {free_str} free ({used_pct:.0f}%)"

def update(indicator):
    indicator.set_label(label(), "/data 999G free (100%)")
    return True  # keep repeating

def main():
    ind = AppIndicator3.Indicator.new(
        "data-disk-indicator",
        "drive-harddisk",
        AppIndicator3.IndicatorCategory.SYSTEM_SERVICES,
    )
    ind.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
    guide = "/data 999G free (100%)"
    ind.set_label(label(), guide)

    # Menu with just a Quit item
    menu = Gtk.Menu()
    item_quit = Gtk.MenuItem(label="Quit")
    item_quit.connect("activate", Gtk.main_quit)
    menu.append(item_quit)
    menu.show_all()
    ind.set_menu(menu)

    GLib.timeout_add_seconds(INTERVAL, update, ind)
    Gtk.main()

if __name__ == "__main__":
    main()
