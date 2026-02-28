import os
import codecs

def fix_mojibake(filename):
    try:
        with open(filename, 'rb') as f:
            content = f.read()
            
        try:
            text_utf8 = content.decode('utf-8')
            # Check for typical utf-8 to cp1251 mojibake characters
            if 'Р' in text_utf8 or 'С' in text_utf8:
                try:
                    # try to see if it fixes it
                    fixed_text = text_utf8.encode('cp1251').decode('utf-8')
                    # if it succeeds, it implies it was indeed mojibake (double-encoded)
                    with open(filename, 'w', encoding='utf-8') as fw:
                        fw.write(fixed_text)
                    print(f'Fixed double-encoded mojibake in {filename}')
                    return
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
        except UnicodeDecodeError:
            # Maybe it's just cp1251?
            try:
                text_1251 = content.decode('windows-1251')
                with open(filename, 'w', encoding='utf-8') as fw:
                    fw.write(text_1251)
                print(f'Converted from windows-1251 to utf-8: {filename}')
                return
            except UnicodeDecodeError:
                pass
                
        # Also check if it's utf-8 with utf-8 BOM
        if content.startswith(codecs.BOM_UTF8):
            with open(filename, 'w', encoding='utf-8') as fw:
                fw.write(content.decode('utf-8-sig'))
            print(f'Removed BOM and saved as utf-8: {filename}')
            return
            
    except Exception as e:
        print(f"Error processing {filename}: {e}")

template_dir = r"c:\realy_work\qwerdsaw12\Emercom\app\templates"
static_dir = r"c:\realy_work\qwerdsaw12\Emercom\app\static"

for directory in [template_dir, static_dir]:
    for root, _, files in os.walk(directory):
        for f in files:
            if f.endswith('.html') or f.endswith('.js') or f.endswith('.css'):
                fix_mojibake(os.path.join(root, f))
