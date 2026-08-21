# 01v96-editor

Éditeur de scène pour la console Yamaha 01V96 V2 (VCM), en remplacement de
Studio Manager 2 (SM2.app), incompatible avec macOS moderne (binaire
PPC+i386 / Qt 3.3.6).

Communique directement avec la console via son port USB-MIDI natif
(pilote "Yamaha USB-MIDI Driver"), en réimplémentant le protocole SysEx
documenté par Yamaha (`docs/01V96 MIDI SPEC.pdf` et
`docs/01V96 V2 Parameter Change List.xls`), plutôt qu'en rétro-ingéniérant
le binaire de SM2.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Utilisation rapide

```bash
# lister les ports MIDI disponibles (doit montrer "YAMAHA 01V96 Port1".."Port8")
python scripts/list_ports.py

# lire un paramètre (ex: gain EQ grave, voie d'entrée 1 -> channel=0)
python scripts/get_param.py kInputEQ kEQLowG 0

# lancer l'interface graphique (routing, noms, comp/gate, EQ, effet/reverb)
python scripts/run_gui.py
```

L'interface graphique propose un port MIDI réel (si la console est branchée)
ou un mode **"Simulation hors ligne"** qui se contente de journaliser les
trames SysEx sans rien envoyer — utile pour valider l'interface sans la
console physique.

## Structure

- `yamaha01v96/sysex.py` — construction/analyse des messages SysEx (adressage,
  encodage des valeurs sur des octets 7 bits).
- `yamaha01v96/params.py` — charge `docs/parameter_map.json` (généré depuis le
  fichier `.xls` officiel) et fait le lien nom de paramètre -> adresse SysEx.
- `yamaha01v96/midi.py` — accès CoreMIDI (via `mido`/`python-rtmidi`).
- `yamaha01v96/console.py` — API haut niveau : `set_parameter`/`request_parameter`.
- `yamaha01v96/gui.py` — interface graphique Tkinter (onglets EQ / Dynamique /
  Routing / Noms / Effet-Reverb), avec mode simulation hors ligne.
- `docs/` — documentation Yamaha de référence + captures d'écran de
  l'éditeur officiel (`docs/screens/`) pour inspiration graphique.

## État actuel / limites connues

- Le format d'adresse générique `F0 43 1n/3n 3E [model_id] [addr_type] ee pp cc
  dd...dd F7` est confirmé par `01V96 MIDI SPEC.pdf` (section 5.8.3). Le couple
  `model_id`/`addr_type` **varie selon le paramètre** (pas toujours `7F`/`01`) :
  - `7F`/`01` (Universal, Edit buffer) : paramètres temps réel (EQ, Comp,
    Gate, Fader...).
  - `0D`/`02` (01V96, Patch data) : noms de canaux/bus, routing d'insert.
  - `0D`/`03` (01V96, Setup data) : automix, bibliothèques, config routing.
  - `0D`/`04` (01V96, Backup data) : bibliothèques EQ/effets/automix.
  Chaque `ParamDef` (voir `params.py`) porte son propre `model_id`/`addr_type`
  extrait du fichier `.xls`, et `sysex.py`/`console.py` les utilisent tels
  quels — aucun code ne doit re-hardcoder `7F`/`01`.
- L'encodage exact des octets de données (`dd...dd`) pour les valeurs
  multi-octets (nombre d'octets 7 bits utilisés selon min/max) est une
  **hypothèse best-effort** (voir commentaire en tête de `sysex.py`) : le PDF
  ne donne pas la formule exacte, seulement le principe général. À valider
  empiriquement sur la console réelle (envoyer une valeur connue, comparer
  avec l'écran de la console ou une réponse "Parameter request").
- Les paramètres d'effet (`kEffectParam1..N`, reverb) ont une adresse SysEx
  connue mais leur signification exacte dépend du type d'effet sélectionné
  (voir "effect specifications", non présent dans le fichier `.xls`).
