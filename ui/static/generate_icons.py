#!/usr/bin/env python3
"""
Generate PWA icons from a source image
"""

from PIL import Image
import os

# Icon sizes needed for PWA
SIZES = [72, 96, 128, 144, 152, 192, 384, 512]

def create_icons_from_source(source_path, icons_dir):
    """Generate all icon sizes from a source image"""
    # Open source image
    img = Image.open(source_path)
    
    # Convert to RGBA if necessary
    if img.mode != 'RGBA':
        img = img.convert('RGBA')
    
    print(f"Source image: {source_path} ({img.size[0]}x{img.size[1]})")
    
    for size in SIZES:
        # Resize with high quality
        resized = img.resize((size, size), Image.Resampling.LANCZOS)
        
        # Convert to RGB for JPEG/PNG output
        if resized.mode == 'RGBA':
            # Create background
            background = Image.new('RGB', (size, size), (99, 102, 241))  # Primary color
            background.paste(resized, mask=resized.split()[3])  # Use alpha as mask
            resized = background
        
        filename = f'icon-{size}x{size}.png'
        filepath = os.path.join(icons_dir, filename)
        resized.save(filepath, 'PNG')
        print(f"✓ Created {filename}")

def main():
    """Generate all icon sizes"""
    script_dir = os.path.dirname(__file__)
    icons_dir = os.path.join(script_dir, 'icons')
    
    # Look for the source icon
    source_files = [
        os.path.join(icons_dir, 'Gemini_Generated_Image_ioir6wioir6wioir.png'),
        os.path.join(script_dir, 'Gemini_Generated_Image_ioir6wioir6wioir.png'),
    ]
    
    source_path = None
    for path in source_files:
        if os.path.exists(path):
            source_path = path
            break
    
    if not source_path:
        print("Error: Source icon not found!")
        print("Please place your icon at: ui/static/icons/")
        return
    
    os.makedirs(icons_dir, exist_ok=True)
    
    print("Generating PWA icons from source...")
    create_icons_from_source(source_path, icons_dir)
    print(f"\n✓ All icons generated in {icons_dir}/")

if __name__ == '__main__':
    main()
