# Images Directory

System icons displayed in the UI (e.g., next to system info in Local System Stats).

## Directory Structure

```
images/
├── jetson-orin.png        # Jetson AGX Orin
├── jetson-thor.png        # Jetson AGX Thor
├── dgx-spark.png          # DGX Spark
├── macbook.png            # MacBook (generic)
├── mac-mini.png           # Mac mini
├── imac.png               # iMac
├── pc.png                 # Generic PC/Workstation
└── README.md              # This file
```

## Usage

Images are served at: `https://yourserver:8090/images/filename.png`

```javascript
// Example in index.html
const icon = document.createElement('img');
icon.src = '/images/jetson-thor.png';
icon.alt = 'Jetson AGX Thor';
icon.className = 'system-icon';
```

## Icon Guidelines

### Format & Size
- **Format:** PNG with transparency (or WebP)
- **Dimensions:** 128x128px (displayed at 48-64px)
- **File Size:** < 50KB per icon
- **Background:** Transparent
- **Style:** Simple, flat, icon-like (not photorealistic)

### File Naming

Use simple, lowercase names with hyphens:

```
jetson-orin.png          # Jetson Orin
jetson-thor.png          # Jetson Thor
dgx-spark.png            # DGX Spark
macbook.png              # MacBook (generic, works for all sizes)
mac-mini.png             # Mac mini
imac.png                 # iMac
pc.png                   # Generic PC
```

## Finding/Creating Icons

### Free Icon Sources (Recommended)
- **Flaticon** (https://www.flaticon.com) - Free with attribution
- **Icons8** (https://icons8.com) - Free with attribution
- **Iconfinder** (https://www.iconfinder.com) - Filter by free
- **The Noun Project** (https://thenounproject.com) - CC-licensed

**Search terms:**
- "laptop icon"
- "computer icon"
- "server icon"
- "workstation icon"

### Creating Custom Icons
- Use Figma, Sketch, or Inkscape
- Keep design simple and flat
- Match the UI's color scheme
- Export at 2x size (256x256) then downscale

### For Mac Icons
- Use **generic representations** (not actual product photos)
- Simple laptop/desktop outlines
- Avoid Apple trademarked designs
- Keep it abstract/iconic

## Example: JavaScript Mapping

```javascript
// Simple icon mapping based on platform/product
function getSystemIcon(stats) {
    const productName = stats.product_name || '';
    const boardName = stats.board_name || '';
    const platform = stats.platform || '';

    // Jetson boards
    if (boardName.includes('Thor') || productName.includes('Thor')) {
        return '/images/jetson-thor.png';
    }
    if (boardName.includes('Orin') || productName.includes('Orin')) {
        return '/images/jetson-orin.png';
    }

    // DGX systems
    if (productName.includes('DGX Spark')) {
        return '/images/dgx-spark.png';
    }

    // Mac products (generic icons, not product photos)
    if (productName.includes('MacBook')) {
        return '/images/macbook.png';
    }
    if (productName.includes('Mac mini')) {
        return '/images/mac-mini.png';
    }
    if (productName.includes('iMac')) {
        return '/images/imac.png';
    }

    // Generic PC fallback
    return '/images/pc.png';
}

// Usage
const iconPath = getSystemIcon(stats);
if (iconPath) {
    imgElement.src = iconPath;
    imgElement.style.display = 'block';
}
```

## CSS Styling

```css
.system-icon {
    width: 48px;
    height: 48px;
    object-fit: contain;
    opacity: 0.9;
}

/* Optional hover effect */
.system-icon:hover {
    opacity: 1;
    transform: scale(1.05);
}
```

## Optimization

Before adding images:
1. Resize to appropriate dimensions (64x64 or 128x128)
2. Optimize with tools like:
   - `pngquant` for PNG
   - `cwebp` for WebP
   - ImageOptim (Mac)
3. Keep total size under 50KB per image
4. Consider using WebP format for better compression

## Fallback

Always provide a fallback for missing images:

```javascript
img.onerror = function() {
    // Hide image or show default icon
    this.style.display = 'none';
};
```

