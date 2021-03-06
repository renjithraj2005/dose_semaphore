#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Dose - Automated semaphore GUI showing the state in test driven development
Copyright (C) 2012 Danilo de Jesus da Silva Bellini

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <http://www.gnu.org/licenses/>.

Created on Sat Sep 29 2012
danilo [dot] bellini [at] gmail [dot] com
"""

from __future__ import division, print_function
import wx
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from subprocess import Popen, PIPE
from datetime import datetime
import os
import time

# TODO:
# - Find a way to get transparency work with wxGTK. However, it seems that
#   SetTransparency doesn't work with wxGTK (Linux) after window creation:
#     http://trac.wxwidgets.org/ticket/13240
#   but self.HasTransparentBackground() inside DojoGraphicalSemaphore
#   opacity setter returns False, and it shouldn't.
# - Remove flicker when resizing (wxGTK). Why wx.FULL_REPAINT_ON_RESIZE
#   and wx.NO_FULL_REPAINT_ON_RESIZE behaves equally (wxGTK)?

# Thresholds and other constants
PI = 3.141592653589793
MIN_WIDTH = 9 # Pixels
MIN_HEIGHT = 9
FIRST_WIDTH = 100 # FIRST_* are starting values
FIRST_HEIGHT = 300
MIN_OPACITY = 0x10 # Color intensity in byte range
MAX_OPACITY = 0xff
FIRST_OPACITY = 0x70
MOUSE_TIMER_WATCH = 20 # ms
LED_OFF = 0x3f3f3f # Color
LED_RED = 0xff0000
LED_YELLOW = 0xffff00
LED_GREEN = 0x00ff00
FIRST_LEDS = (LED_RED, LED_YELLOW, LED_GREEN) # Standby colors
LEDS_RED = (LED_RED, LED_OFF, LED_OFF)
LEDS_YELLOW = (LED_OFF, LED_YELLOW, LED_OFF)
LEDS_GREEN = (LED_OFF, LED_OFF, LED_GREEN)
BACKGROUND_COLOR = 0x000000
BACKGROUND_BORDER_COLOR = 0x7f7f7f7f
TERMINAL_WIDTH = 79

def rounded_rectangle_region(width, height, radius):
  """
  Returns a rounded rectangle wx.Region
  """
  bmp = wx.EmptyBitmapRGBA(width, height) # Mask color is #000000
  dc = wx.MemoryDC(bmp)
  dc.Brush = wx.Brush((255,) * 3) # Any non-black would do
  dc.DrawRoundedRectangle(0, 0, width, height, radius)
  dc.SelectObject(wx.NullBitmap)
  bmp.SetMaskColour((0,) * 3)
  return wx.RegionFromBitmap(bmp)

def int_to_color(color_int):
  """
  Returns a color string from a 24bits integer
  """
  return "#{0:06x}".format(color_int)# + "7f"

def int_to_darkened_color(color_int):
  """
  Returns a color string from a 24bits integer with all components halved
  """
  return int_to_color((color_int >> 1) & 0x7f7f7f) # Divide by 2 every color


class DoseGraphicalSemaphore(wx.Frame):
  """
  Graphical semaphore frame widget (window)
  Property "leds" contains the colors that should be seen (a 3-tuple with
  24bit integers but can receive any sequence that quacks alike).
  Property "opacity" controls transparency alpha factor from 0x00 (fully
  transparency) to 0xff (no transparency)
  Property "flip" is a boolean that can reverse the led order
  This class knows nothing about the led color meaning, and just print them
  at the screen.
  """
  def __init__(self, parent, leds=FIRST_LEDS,
               width=FIRST_WIDTH, height=FIRST_HEIGHT,
               opacity=FIRST_OPACITY, flip=False):
    frame_style = (wx.FRAME_SHAPED |     # Allows wx.SetShape
                   wx.FRAME_NO_TASKBAR |
                   wx.STAY_ON_TOP |
                   wx.NO_BORDER
                  )
    super(DoseGraphicalSemaphore, self).__init__(parent, style=frame_style)
    #self.BackgroundStyle = wx.BG_STYLE_CUSTOM # Avoids flicker
    self.Bind(wx.EVT_ERASE_BACKGROUND, lambda evt: None)
    self.Bind(wx.EVT_WINDOW_CREATE,
              lambda evt: self.SetTransparent(FIRST_OPACITY)
             ) # Needed to at least have transparency on wxGTK
    self._paint_width, self._paint_height = 0, 0 # Ensure update_sizes at
                                                 # first on_paint
    self.ClientSize = (width, height)
    self._flip = flip
    self.opacity = opacity
    self.Bind(wx.EVT_PAINT, self.on_paint)
    self.leds = leds # Refresh!

  @property
  def leds(self):
    return self._leds

  @leds.setter
  def leds(self, values):
    if len(values) != 3:
      raise ValueError("There should be 3 leds")
    self._leds = tuple(values)
    self.Refresh()

  @property
  def opacity(self):
    return self._opacity

  @opacity.setter
  def opacity(self, value):
    self._opacity = value
    self.SetTransparent(self.opacity)

  @property
  def flip(self):
    return self._flip

  @flip.setter
  def flip(self, value):
    if self._flip != value:
      self._flip = value
      self.Refresh()

  def on_paint(self, evt):
    if self.ClientSize != (self._paint_width, self._paint_height):
      self._update_sizes()
    self._draw()

  def _update_sizes(self):
    self._paint_width, self._paint_height = self.ClientSize
    self._rotation = -PI/2 if self._paint_width > self._paint_height else 0
    self._tile_size = min(max(self._paint_width, self._paint_height),
                          min(self._paint_width, self._paint_height) * 3
                         ) / 3
    self._border = self._tile_size * .15
    self._frame_pen_width = self._border / 3 # Half clipped (frame shape)
    self._pen_width = self._border / 4
    dist_circles = self._border / 5
    self._radius = (self._tile_size - 2 * self._pen_width - dist_circles) / 2
    self.SetShape(rounded_rectangle_region(self._paint_width,
                                           self._paint_height,
                                           self._border)
                 )

  def _draw(self):
    dc = wx.AutoBufferedPaintDCFactory(self)
    gc = wx.GraphicsContext.Create(dc) # Anti-aliasing
    gc.Translate(self._paint_width / 2,
                 self._paint_height / 2) # Center

    # Draw the background
    gc.SetBrush(wx.Brush(int_to_color(BACKGROUND_COLOR)))
    gc.SetPen(wx.Pen(int_to_color(BACKGROUND_BORDER_COLOR),
                     width=self._frame_pen_width)
             )
    gc.DrawRoundedRectangle(-self._paint_width / 2,
                            -self._paint_height / 2,
                            self._paint_width,
                            self._paint_height,
                            self._border)

    # Draw the LEDs
    gc.Rotate(self._rotation)
    if self.flip:
      gc.Rotate(PI)
    gc.Translate(0, -self._tile_size)

    for led in self.leds: # The led is an integer with the color
      gc.SetBrush(wx.Brush(int_to_color(led)))
      gc.SetPen(wx.Pen(int_to_darkened_color(led),
                       width=self._pen_width)
               )
      gc.DrawEllipse(-self._radius,
                     -self._radius,
                     2 * self._radius,
                     2 * self._radius)
      gc.Translate(0, self._tile_size)


class DoseInteractiveSemaphore(DoseGraphicalSemaphore):
  """
  Just a DojoGraphicalSemaphore, but now responsive to left click
  """
  def __init__(self, parent):
    super(DoseInteractiveSemaphore, self).__init__(parent)
    self._timer = wx.Timer(self)
    self.Bind(wx.EVT_LEFT_DOWN, self.on_left_down)
    self.Bind(wx.EVT_TIMER, self.on_timer, self._timer)

  def on_left_down(self, evt):
    self._key_state = None # Ensures initialization
    self.on_timer(evt)

  def on_timer(self, evt):
    """
    Keep watching the mouse displacement via timer
    Needed since EVT_MOVE doesn't happen once the mouse gets outside the
    frame
    """
    ctrl_is_down = wx.GetKeyState(wx.WXK_CONTROL)
    shift_is_down = wx.GetKeyState(wx.WXK_SHIFT)
    ms = wx.GetMouseState()

    # New initialization when keys pressed change
    if self._key_state != (ctrl_is_down, shift_is_down):
      self._key_state = (ctrl_is_down, shift_is_down)

      # Keep state at click
      self._click_ms_x, self._click_ms_y = ms.x, ms.y
      self._click_frame_x, self._click_frame_y = self.Position
      self._click_frame_width, self._click_frame_height = self.ClientSize
      self._click_opacity = self.opacity

      # Avoids refresh when there's no move (stores last mouse state)
      self._last_ms = ms.x, ms.y

      # Quadrant at click (need to know how to resize)
      width, height = self.ClientSize
      self._quad_signal_x = 1 if (self._click_ms_x -
                                  self._click_frame_x) / width > .5 else -1
      self._quad_signal_y = 1 if (self._click_ms_y -
                                  self._click_frame_y) / height > .5 else -1

    # "Polling watcher" for mouse left button while it's kept down
    if ms.leftDown:
      if self._last_ms != (ms.x, ms.y): # Moved?
        self._last_ms = (ms.x, ms.y)
        delta_x = ms.x - self._click_ms_x
        delta_y = ms.y - self._click_ms_y

        # Change transparency / opacity
        if shift_is_down:
          self.opacity = max(MIN_OPACITY,
                             min(MAX_OPACITY, self._click_opacity - delta_y)
                            )

        # Resize
        if ctrl_is_down:
          # New size
          new_w = max(MIN_WIDTH, self._click_frame_width +
                                 2 * delta_x * self._quad_signal_x
                     )
          new_h = max(MIN_HEIGHT, self._click_frame_height +
                                  2 * delta_y * self._quad_signal_y
                     )
          self.ClientSize = new_w, new_h
          self.SendSizeEvent() # Needed for wxGTK

          # Center should be kept
          center_x = self._click_frame_x + self._click_frame_width / 2
          center_y = self._click_frame_y + self._click_frame_height / 2
          self.Position = (center_x - new_w / 2,
                           center_y - new_h / 2)

          self.Refresh()

        # Move the window
        if not (ctrl_is_down or shift_is_down):
          self.Position = (self._click_frame_x + delta_x,
                           self._click_frame_y + delta_y)

      # Since left button is kept down, there should be another one shot
      # timer event again, without creating many timers like wx.CallLater
      self._timer.Start(MOUSE_TIMER_WATCH, True)


class DosePopupMenu(wx.Menu):

  def __init__(self, hc, watching):
    """
    Dose pop-up menu widget constructor
    The "hc" (handler class) parameter should be a class with all the needed
    callback methods.
    """
    super(DosePopupMenu, self).__init__()

    # Define menu data
    menu_items = [("Flip", hc.on_flip),
                  (None, None),
                 ]
    if watching:
      menu_items.extend([("Stop!\tDouble Click", hc.on_stop),
                         (None, None),
                         ("Red", hc.on_red),
                         ("Yellow", hc.on_yellow),
                         ("Green", hc.on_green),
                        ])
    else:
      menu_items.extend([("Start!\tDouble Click", hc.on_start),
                         (None, None),
                         ("Directory to watch...", hc.on_directory_to_watch),
                         ("Define call string...", hc.on_define_call_string),
                        ])
    menu_items.extend([(None, None),
                       ("Close\tAlt+F4", hc.on_close),
                       (None, None),
                       ("Help and about...", hc.on_help_and_about),
                      ])

    # Create the menu items
    for txt, callback in menu_items:
      if txt is None:
        self.AppendSeparator()
      else:
        item = wx.MenuItem(self, wx.ID_ANY, txt)
        self.AppendItem(item)
        self.Bind(wx.EVT_MENU, callback, item)


class DoseWatcher(object):
  """
  A class to watch
  """
  def __init__(self, printer=print):
    self.directory = os.path.curdir # Default directory
    self.call_string = ""
    self._watching = False
    self.printer = printer

  @property
  def watching(self):
    return self._watching

  def has_call_string(self):
    return len(self.call_string.strip()) > 0

  def start(self, func_err, func_wait, func_ok, func_stop):
    """
    Starts watching. The three args are loggers or aesthetical functions to be
    called when the state changes (e.g., there's something being handled).
    """
    assert not self.watching

    class DoseSomethingChangedEventHandler(FileSystemEventHandler):

      def on_any_event(handler, evt=None):
        """
        Handle the event "directory has changed its contents somehow"
        Using "handler" instead of "self" for class instance, so "self" is a
        DoseWatcher instance, and self.printer may change while watching.
        That's also useful to store "self.last_mtime", since there's a new
        handler instance for each event.
        """
        # ---------------------------------------
        # Event filters (to avoid unuseful calls)
        # ---------------------------------------
        if evt is None: # First event, called directly
          self.last_mtime = time.time() # Last change happened now
        else:
          # Neglect calls from compiled files
          path = evt.src_path
          for extension in ".pyc; .pyo".split(";"):
            if path.endswith(extension.strip()):
              return

          # Neglect calls that happens too fast by waiting few hundreds of
          # milliseconds, and "calls from the past"
          time.sleep(.2)
          try:
            new_mtime = os.path.getmtime(path)
            if new_mtime <= self.last_mtime:
              return
            self.last_mtime = new_mtime
          except OSError:
            pass # File deleted, probably. We don't have the time to update,
                 # so let's run the calling string

        # ------------------
        # Subprocess calling
        # ------------------
        # State changed: "waiting" for a subprocess (still not called)
        func_wait()

        # Logging
        timestamp = datetime.now()
        self.printer() # Skip one line
        self.printer("=" * TERMINAL_WIDTH)
        self.printer("Dose call at {0}".format(timestamp)
                                       .center(TERMINAL_WIDTH)
                    )

        # Subprocess calling
        try:
          process = Popen(self.call_string.split(), stdout=PIPE, stderr=PIPE)
          out, err = process.communicate()
          returned_value = process.wait()
        except Exception, e:
          self.stop() # Watching no more
          self.printer("Error while calling".center(TERMINAL_WIDTH))
          self.printer("=" * TERMINAL_WIDTH)
          func_stop(e)
          return

        # Get the data from the process\\
        for data, name in [(out, "Output data"), (err, "Error data")]:
          if len(data) > 0:
            self.printer(name.center(TERMINAL_WIDTH))
            self.printer("=" * TERMINAL_WIDTH)
            self.printer(data)
            self.printer("=" * TERMINAL_WIDTH)

        # Informative message when there's no output data
        if (len(out) == 0) and (len(err) == 0):
          self.printer("No data".center(TERMINAL_WIDTH))
          self.printer("=" * TERMINAL_WIDTH)

        # Updates the state
        if returned_value == 0:
          func_ok()
        else:
          func_err()

    # Starts the watchdog observer
    self._observer = Observer()
    self._observer.schedule(DoseSomethingChangedEventHandler(),
                            path=self.directory,
                            recursive=True)
    DoseSomethingChangedEventHandler().on_any_event()
    self._observer.start()
    self._watching = True

  def stop(self):
    if self.watching:
      self._observer.stop()
      self._observer.join()
      self._watching = False


class DoseMainWindow(DoseInteractiveSemaphore, DoseWatcher):

  def __init__(self, parent):
    DoseInteractiveSemaphore.__init__(self, parent)
    DoseWatcher.__init__(self)
    self.SetTitle("Disabled - dose") # Seen by the window manager
    self.popmenu = {k:DosePopupMenu(self, k) for k in (True, False)}

    self.Bind(wx.EVT_RIGHT_DOWN, self.on_right_down)
    self.Bind(wx.EVT_LEFT_DCLICK, self.on_left_dclick)
    self.Bind(wx.EVT_CLOSE, self.on_close)

  def on_right_down(self, evt):
    self.PopupMenu(self.popmenu[self.watching], evt.Position)

  def on_left_dclick(self, evt):
    if self.watching:
      self.on_stop()
    else:
      self.on_start()

  def on_flip(self, evt):
    self.flip = not self.flip

  def on_red(self, evt=None):
    self.leds = LEDS_RED

  def on_yellow(self, evt=None):
    self.leds = LEDS_YELLOW

  def on_green(self, evt=None):
    self.leds = LEDS_GREEN

  def on_start(self, evt=None):
    if not self.has_call_string():
      self.on_define_call_string()

    if self.has_call_string():
      self.start(func_err=self.on_red,
                 func_wait=self.on_yellow,
                 func_ok=self.on_green,
                 func_stop=self.on_stop)

  def on_stop(self, evt=None):
    self.stop()
    self.leds = FIRST_LEDS
    if isinstance(evt, Exception):
      msg = "Exception '{0}' raised"
      wx.MessageDialog(self, str(evt),
                       msg.format(repr(evt).split("(")[0]),
                       wx.ICON_INFORMATION | wx.OK
                      ).ShowModal()

  def on_directory_to_watch(self, evt):
    dd = wx.DirDialog(self, message="Directory to watch...",
                      defaultPath=self.directory)
    if dd.ShowModal() == wx.ID_OK:
      self.directory = dd.Path

  def on_define_call_string(self, evt=None):
    msg = "Executable to be called and its arguments:"
    title = "Dose call string"
    ted = wx.TextEntryDialog(self, message=msg, caption=title,
                             defaultValue=self.call_string)
    if ted.ShowModal() == wx.ID_OK:
      self.call_string = ted.Value

  def on_close(self, evt):
    """
    Pop-up menu and "Alt+F4" closing event
    """
    self.stop() # DoseWatcher
    if evt.EventObject is not self: # Avoid deadlocks
      self.Close() # wx.Frame
    evt.Skip()

  def on_help_and_about(self, evt):
    author = "Danilo de Jesus da Silva Bellini"
    email = "danilo [dot] bellini [at] gmail [dot] com"
    abinfo = wx.AboutDialogInfo()
    abinfo.Artists = abinfo.Developers = [" ".join([author, "-", email])]
    abinfo.Copyright = "Copyright (C) 2012 " + author
    abinfo.Description = (
      "Automated semaphore GUI showing the state in test driven development."
      "\n\n"
      "Dose watches one directory for any kind of change\n"
      "new file, file modified, file removed, subdirectory renamed,\n"
      "etc.), including its subdirectories, by using the Python\n"
      "watchdog package. Changes on files ending on '.pyc' and\n"
      "'.pyo' are neglect."
      "\n\n"
      "A *customized* subprocess is called, all its output/error\n"
      "data is left on the shell used to call Dose, and its return\n"
      "value is stored. If the value is zero, the semaphore turns\n"
      "green, else it it turns red. It stays yellow while waiting\n"
      "the subprocess to finish."
      "\n\n"
      "The default directory path to watch is the one used to call\n"
      "Dose. There's no default calling string, but 'nosetests'\n"
      "would be a hint for Python developers. It should work even\n"
      "with other languages TDD tools. To be fast, just open Dose\n"
      "and double click on it, there's no need to lose time with\n"
      "settings."
      "\n\n"
      "The GUI toolkit used in this project is wxPython. You can\n"
      "move the semaphore by dragging it around. Doing so with\n"
      "Ctrl pressed would resize it. With Shift you change its\n"
      "transparency (not available on Linux, for now). The\n"
      "semaphore window always stays on top. A right click would\n"
      "show all options available."
    )
    abinfo.Version = "2012.10.02"
    abinfo.Name = "Dose"
    abinfo.License = (
      "This program is free software: you can redistribute it and/or modify\n"
      "it under the terms of the GNU General Public License as published by\n"
      "the Free Software Foundation, either version 3 of the License, or\n"
      "(at your option) any later version."
      "\n\n"
      "This program is distributed in the hope that it will be useful,\n"
      "but WITHOUT ANY WARRANTY; without even the implied warranty of\n"
      "MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the\n"
      "GNU General Public License for more details."
      "\n\n"
      "You should have received a copy of the GNU General Public License\n"
      "along with this program. If not, see <http://www.gnu.org/licenses/>."
    )
    wx.AboutBox(abinfo)


class DoseApp(wx.App):

  def OnInit(self):
    self.SetAppName("dose")
    wnd = DoseMainWindow(None)
    wnd.Show()
    self.SetTopWindow(wnd)
    return True # Needed by wxPython


if __name__ == "__main__":
    DoseApp(False).MainLoop()
