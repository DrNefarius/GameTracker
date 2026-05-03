from cx_Freeze import setup, Executable
from constants import VERSION

# Dependencies are automatically detected, but it might need
# fine tuning.
build_options = {
    'packages': [
        'tkinter', 'tkinter.filedialog', 'tkinter.messagebox',
        'psutil', 'rapidfuzz', 'pystray',
        'PIL', 'PIL.Image',
    ],
    'excludes': [],
    'include_files': ['gameslisticon.ico'],
    # windows-toasts depends on winrt; cx_Freeze sometimes misses transitive
    # extension modules. Listing it explicitly keeps the frozen build honest.
    'includes': ['windows_toasts'],
}

base = 'gui'

executables = [
    Executable('main.py', base=base, target_name = 'GameTracker', icon='gameslisticon.ico')
]

setup(name='GameTracker',
      version = VERSION,
      description = 'A simple application to manage your cross-platform games library and track your gaming sessions.',
      options = {'build_exe': build_options},
      executables = executables)
