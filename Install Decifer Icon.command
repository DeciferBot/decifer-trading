#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
rm -rf ~/Desktop/Decifer.app
cp -rp "$DIR/Decifer.app" ~/Desktop/
xattr -cr ~/Desktop/Decifer.app 2>/dev/null
echo "Decifer app icon installed on Desktop."
