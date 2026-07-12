import os
import sys
import shutil
import subprocess
import builtins


def _safe_print(*args, **kwargs):
    try:
        builtins.print(*args, **kwargs)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, 'encoding', None) or 'utf-8'
        safe_args = [
            str(arg).encode(encoding, errors='replace').decode(encoding, errors='replace')
            for arg in args
        ]
        builtins.print(*safe_args, **kwargs)


print = _safe_print

def run_build():
    print("===========================================")
    print("☁️  MyCloud Desktop App Builder")
    print("===========================================")

    # Clean up old compiled zip/exe/dmg files and folders inside static/downloads/ to avoid circular bundling inflation
    downloads_dir = os.path.join('static', 'downloads')
    if os.path.exists(downloads_dir):
        for item in os.listdir(downloads_dir):
            if item.endswith('.zip') or item.endswith('.exe') or item.endswith('.dmg') or item == 'MyCloud.app':
                path = os.path.join(downloads_dir, item)
                print(f"🧹 Removing old package from downloads to prevent bundle inflation: {path}")
                try:
                    if os.path.isdir(path):
                        shutil.rmtree(path)
                    else:
                        os.remove(path)
                except Exception as e:
                    print(f"⚠️ Failed to remove {path}: {e}")

    # Ensure output download directories exist
    os.makedirs('static/downloads', exist_ok=True)

    # Base PyInstaller arguments
    pyinstaller_args = [
        'app.py',
        '--name=MyCloud',
        '--noconsole',
        '--clean',
        '--noconfirm'
    ]

    # Platform-specific configurations
    sep = ';' if sys.platform == 'win32' else ':'
    
    # Add templates and static assets
    pyinstaller_args.append(f'--add-data=templates{sep}templates')
    pyinstaller_args.append(f'--add-data=static{sep}static')

    # Add cloudflared tunnel client if it exists locally
    binary_name = 'cloudflared.exe' if sys.platform == 'win32' else 'cloudflared'
    if os.path.exists(binary_name):
        print(f"📦 Bundling tunnel client: {binary_name}")
        pyinstaller_args.append(f'--add-binary={binary_name}{sep}.')
    else:
        print(f"⚠️  No local '{binary_name}' found. The app will build without a bundled tunnel client.")

    if sys.platform == 'darwin':
        # macOS specific options
        pyinstaller_args.append('--windowed')
        pyinstaller_args.append('--icon=static/favicon.png')
    elif sys.platform == 'win32':
        # Windows specific options
        pyinstaller_args.append('--onefile')
        pyinstaller_args.append('--icon=static/favicon.png')

    # Run PyInstaller
    print(f"🚀 Running PyInstaller with arguments: {pyinstaller_args}")
    try:
        import PyInstaller.__main__
        PyInstaller.__main__.run(pyinstaller_args)
    except ImportError:
        print("❌ PyInstaller is not installed in this environment. Run: pip install pyinstaller")
        return

    # Post-build packaging
    print("\n===========================================")
    print("🧹 Post-Build Packaging")
    print("===========================================")

    if sys.platform == 'darwin':
        app_path = 'dist/MyCloud.app'
        zip_output = os.path.abspath('static/downloads/MyCloud-macOS.zip')
        dmg_output = os.path.abspath('static/downloads/MyCloud-macOS.dmg')
        if os.path.exists(app_path):
            print(f"🗜️  Compressing macOS application bundle to ZIP...")
            if os.path.exists(zip_output):
                os.remove(zip_output)
            
            # Use zip utility via shell to preserve macOS metadata/permissions
            try:
                subprocess.run(['zip', '-r', zip_output, 'MyCloud.app'], cwd='dist', check=True)
                print(f"✅ Created ZIP: {zip_output}")
            except Exception as e:
                print(f"❌ Failed to zip app bundle: {e}")

            print(f"📀 Packaging macOS app into DMG installer...")
            if os.path.exists(dmg_output):
                os.remove(dmg_output)
            
            try:
                dmg_temp = 'dist/dmg_temp'
                if os.path.exists(dmg_temp):
                    shutil.rmtree(dmg_temp)
                os.makedirs(dmg_temp)
                
                # Copy the application bundle to the temp folder to preserve it as a single file in the DMG
                shutil.copytree(app_path, os.path.join(dmg_temp, 'MyCloud.app'), symlinks=True)

                subprocess.run([
                    'hdiutil', 'create',
                    '-volname', 'MyCloud',
                    '-srcfolder', dmg_temp,
                    '-ov',
                    '-format', 'UDZO',
                    dmg_output
                ], check=True)
                
                shutil.rmtree(dmg_temp)
                print(f"✅ Created DMG: {dmg_output}")
            except Exception as e:
                print(f"❌ Failed to create DMG: {e}")
        else:
            print("❌ Build output 'dist/MyCloud.app' not found.")

    elif sys.platform == 'win32':
        exe_path = 'dist/MyCloud.exe'
        dest_exe = 'static/downloads/MyCloud-Windows.exe'
        if os.path.exists(exe_path):
            print(f"💾 Copying Windows executable to static downloads...")
            if os.path.exists(dest_exe):
                os.remove(dest_exe)
            shutil.copy2(exe_path, dest_exe)
            print(f"✅ Created: {dest_exe}")
        else:
            print("❌ Build output 'dist/MyCloud.exe' not found.")

    print("\n🎉 Build process finished!")

if __name__ == '__main__':
    run_build()
