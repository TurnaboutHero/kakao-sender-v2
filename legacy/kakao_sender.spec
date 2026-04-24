# kakao_sender.spec
# 빌드 명령어: pyinstaller kakao_sender.spec

from PyInstaller.building.build_main import Analysis, PYZ, EXE

a = Analysis(
    ['kakao_sender.py'],
    pathex=[],
    binaries=[],
    datas=[
        # members.json 이 있으면 함께 포함 (없어도 무방)
        # ('members.json', '.'),
    ],
    hiddenimports=[
        'win32api', 'win32con', 'win32gui',
        'win32clipboard', 'pywintypes',
        'PIL', 'PIL.Image',
        'pyautogui', 'pyperclip',
        'json',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'pytesseract',   # 이번 버전에서 OCR 제거
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='카카오톡_자동발송기',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,       # GUI 모드 (검은 창 안 뜸)
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=None,           # 아이콘 파일 있으면 'icon.ico' 로 변경
)
