#!/bin/bash
set -e

echo "🔨 Building Tlon WebUI..."

# Check if web directory exists
if [ ! -d "web" ]; then
    echo "❌ Error: web/ directory not found"
    exit 1
fi

# Check if npm is available
if ! command -v npm &> /dev/null; then
    echo "❌ Error: npm is not installed"
    exit 1
fi

# 1. Build frontend
echo "📦 Installing dependencies..."
cd web
npm ci

echo "🏗️  Building..."
npm run build
cd ..

# 2. Copy build output to Python package
echo "📂 Copying build output..."
rm -rf src/tlon/webui
mkdir -p src/tlon/webui
cp -r web/dist/* src/tlon/webui/

# Verify index.html exists
if [ ! -f "src/tlon/webui/index.html" ]; then
    echo "❌ Error: index.html not found in build output"
    exit 1
fi

echo "✅ WebUI built successfully to src/tlon/webui/"

# 3. Build Python package
echo "📦 Building Python package..."
uv build

echo ""
echo "🎉 Build complete!"
echo ""
echo "To install:"
echo "  uv tool install dist/tlon-*.whl --force"
echo ""
echo "To run:"
echo "  tlon server"
echo ""
echo "Then open: http://localhost:8080/ui"
