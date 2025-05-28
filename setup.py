from cx_Freeze import setup, Executable
from constants import VERSION

# Dependencies are automatically detected, but it might need
# fine tuning.
build_options = {'packages': [], 'excludes': [], 'include_files': ['gameslisticon.ico']}

base = 'gui'

executables = [
    Executable('main.py', base=base, target_name = 'GameTracker', icon='gameslisticon.ico')
]

setup(name='GameTracker',
      version = VERSION,
      description = 'A simple application to manage your cross-platform games library and track your gaming sessions.',
      options = {'build_exe': build_options},
      executables = executables)
