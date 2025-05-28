from cx_Freeze import setup, Executable

# Dependencies are automatically detected, but it might need
# fine tuning.
build_options = {'packages': [], 'excludes': [], 'include_files': ['gameslisticon.ico']}

base = 'gui'

executables = [
    Executable('new_main.py', base=base, target_name = 'GameTracker', icon='gameslisticon.ico')
]

setup(name='GameTracker',
      version = '1.5',
      description = 'A simple application to manage your cross-platform games library and track your gaming sessions.',
      options = {'build_exe': build_options},
      executables = executables)
