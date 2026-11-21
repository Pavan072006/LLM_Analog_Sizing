#!/usr/bin/env bash
echo "== ngspice ==";  ngspice --version 2>&1 | grep -i ngspice | head -2
echo "== PDK ==";      echo "PDK_ROOT=$PDK_ROOT  PDK=$PDK"
ls -d ${PDK_ROOT:-/foss/pdks}/gf180mcu* 2>/dev/null || echo "NO gf180mcu folder in PDK_ROOT"
for d in ${PDK_ROOT:-/foss/pdks}/gf180mcu*/libs.tech/ngspice; do echo "  $d:"; ls "$d" | head -8; done 2>/dev/null
echo "== tools ==";    for t in magic netgen klayout xschem python3; do printf "  %-8s " $t; command -v $t || echo MISSING; done
echo "== python ==";   python3 -c "import numpy, matplotlib; print('  numpy', numpy.__version__, '| matplotlib', matplotlib.__version__)"
python3 -c "import openai; print('  openai', openai.__version__)" 2>/dev/null || echo "  openai MISSING -> pip install openai  (add --break-system-packages if pip refuses)"
python3 -c "import glayout; print('  glayout OK')" 2>/dev/null || echo "  glayout MISSING (only needed tomorrow for layout)"
