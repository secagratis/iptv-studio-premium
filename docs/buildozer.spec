[app]

# (str) Title of your application
title = IPTV Studio Premium

# (str) Package name
package.name = iptvstudiopremium

# (str) Package domain (needed for android packaging)
package.domain = org.iptvstudio

# (str) Source code where the main.py live
source.dir = .

# (list) Source files to include (let empty to include all the files)
source.include_exts = py,png,jpg,kv,atlas,json,xml

# (str) Application versioning (method 1)
version = 1.0.0

# (list) Application requirements
# Nota: openssl e certifi aiutano per HTTPS/TLS su Android
requirements = python3,kivy,pyjnius,urllib3,certifi,openssl

# (str) Presplash of the application
#presplash.filename = %(source.dir)s/data/presplash.png

# (str) Icon of the application
icon.filename = %(source.dir)s/icon.png

# (str) Supported orientations (landscape ideale per TV Box e Tablet)
orientation = landscape

# (list) List of service to declare
#services = NAME:ENTRYPOINT_TO_PY

#
# OSX SPECIFIC
#

#
# android specific
#

# (bool) Indicate if the application should be fullscreen or not
fullscreen = 1

# (string) Presplash background color (for android presplash)
# Supported formats are #RRGGBB #AARRGGBB or one of the following names:
# red, blue, green, black, white, gray, cyan, magenta, yellow, lightgray,
# darkgray, grey, lightgreen, lightblue, darkred, darkgreen, darkblue
android.presplash_color = #0D1426

# (string) presplash animation effect
# fade, slide_on_top, none
#android.presplash_animation = fade

# (list) Permissions
android.permissions = INTERNET,ACCESS_NETWORK_STATE,WAKE_LOCK

# (int) Target Android API, should be as high as possible.
android.api = 36

# (int) Minimum API your APK will support.
android.minapi = 21

# (int) Android SDK version to use
android.sdk = 34

# (str) Android NDK version to use
android.ndk = 25b

# (bool) Use --private data storage (True) or --dir public storage (False)
android.private_storage = True

# (str) Android entry point, default is ok for Kivy
#android.entrypoint = org.kivy.android.PythonActivity

# (list) Android app theme, default is ok for Kivy
#android.apptheme = @android:style/Theme.NoTitleBar

# (list) Pattern to allow for the app
#android.whitelist =

# (str) Path to a custom whitelist file
#android.whitelist_src =

# (str) Path to a custom blacklist file
#android.blacklist_src =

# (list) List of Java .jar files to add to the libs so that they can be included in the application.
#android.add_jars = foo.jar,bar.jar

# (list) List of Java files to add to the android project (can be str or a comma separated list of paths)
#android.add_src =

# (list) Gradle dependencies to add
# Necessarie per AndroidExoPlayerWindow (androidx.media3.*)
android.gradle_dependencies = androidx.media3:media3-exoplayer:1.5.1,androidx.media3:media3-exoplayer-hls:1.5.1,androidx.media3:media3-exoplayer-dash:1.5.1,androidx.media3:media3-datasource:1.5.1

# (bool) Enable AndroidX support. Enable when 'android.gradle_dependencies' uses androidx.*
android.enable_androidx = True

# (list) Packaging options to add
#android.add_aars =

# (list) Java classes to add as activities to the android manifest
#android.add_activities =

# (str) XML file to include as an additional way to appoint permissions and features
#android.manifest.intent_filters =

# (str) launchMode to set for the main activity
#android.manifest.launch_mode = standard

# (list) Android additional libraries to copy into libs/armeabi
#android.add_libs_armeabi = libs/android/*.so
#android.add_libs_armeabi_v7a = libs/android-v7/*.so
#android.add_libs_arm64_v8a = libs/arm64-v8a/*.so
#android.add_libs_x86 = libs/x86/*.so
#android.add_libs_mips =

# (bool) Indicate whether the screen should always be on
android.wakelock = True

# (list) Android application meta-data to set (key=value format)
#android.meta_data =

# (list) Android library project to add (will be added as a
# project.properties afterwards.)
#android.library_references =

# (str) Android logcat filters to use
#android.logcat_filters = *:S python:D Kivy:D

# (bool) Copy library instead of making a libpymodules.so
#android.copy_libs = 1

# (list) The Android archs to build for, choices: armeabi-v7a, arm64-v8a, x86, x86_64
# Includi armeabi-v7a per TV Box / dispositivi meno recenti e arm64-v8a per tablet/TV moderni
android.archs = arm64-v8a, armeabi-v7a

# (bool) enables Android auto backup feature (Android API >=23)
android.allow_backup = True

# (str) XML custom manifest per supporto Android TV / Box TV (touchscreen opzionale + LEANBACK)
android.manifest = manifest_tv_tablet.xml

[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug (with command output))
log_level = 2

# (int) Display warning if buildozer is run as root (not recommended)
warn_on_root = 1
