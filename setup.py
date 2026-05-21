import sys
from pathlib import Path

from cx_Freeze import setup, Executable
from constants import VERSION


def _winrt_build_extras():
    """Collect winrt / windows-toasts deps for frozen Windows builds.

    cx_Freeze 8.3 does not ship the winrt module hook (added in 8.5). Without
    the native ``_winrt_*.pyd`` extensions, ``NotificationData.values[...]``
    fails with ``collections has no attribute '_IMap'`` at runtime.
    """
    if sys.platform != 'win32':
        return [], []

    packages = [
        'windows_toasts',
        'winrt',
        'winrt.runtime',
        'winrt.system',
        'winrt.windows',
        'winrt.windows.foundation',
        'winrt.windows.foundation.collections',
        'winrt.windows.ui.notifications',
        'winrt.windows.data.xml.dom',
    ]
    includes = [
        'winrt._winrt_windows_foundation_collections',
        'winrt._winrt_windows_foundation',
        'winrt._winrt_windows_ui_notifications',
        'winrt._winrt_windows_data_xml_dom',
    ]

    # Pick up any additional pywinrt projection binaries (version bumps, etc.).
    try:
        import site

        for site_dir in site.getsitepackages():
            winrt_dir = Path(site_dir) / 'winrt'
            if not winrt_dir.is_dir():
                continue
            for path in winrt_dir.glob('_winrt_*.pyd'):
                mod = f'winrt.{path.name.split(".cp")[0]}'
                if mod not in includes:
                    includes.append(mod)
            break
    except Exception:
        pass

    return packages, includes


_WINRT_PACKAGES, _WINRT_INCLUDES = _winrt_build_extras()

# Dependencies are automatically detected, but it might need fine tuning.
build_options = {
    'packages': [
        'tkinter', 'tkinter.filedialog', 'tkinter.messagebox',
        'psutil', 'rapidfuzz', 'pystray',
        'PIL', 'PIL.Image',
        *_WINRT_PACKAGES,
    ],
    'excludes': [],
    'include_files': ['gameslisticon.ico'],
    'includes': _WINRT_INCLUDES,
}

base = 'gui'

executables = [
    Executable('main.py', base=base, target_name='GameTracker',
               icon='gameslisticon.ico')
]

setup(name='GameTracker',
      version=VERSION,
      description='A simple application to manage your cross-platform games '
                  'library and track your gaming sessions.',
      options={'build_exe': build_options},
      executables=executables)
