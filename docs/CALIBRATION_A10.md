# Guide de Calibrage et d'Étalonnage Couplé — Poêle Nobis A10

Ce document détaille le fonctionnement du moteur d'étalonnage gravimétrique et de tarage de combustion (Mélange P/A) pour la gamme de poêles à granulés **Nobis A10** (A10 C Light, Light Coax, Light 2020, Style, Style Coax) équipés de la carte mère **Micronova T033.NBS.AIR.MB01** et de la radiocommande **R026.NBS.AIR.UI01**.

---

## 1. Principes et Gestion des Phases Opérationnelles

Pour éliminer tout biais lors d'un étalonnage réel en conditions de vie (où le poêle alterne entre démarrages à froid, rallumages à tiède, régulation climatique, nettoyages automatiques du brasero et extinction), `Nobis.Supervisor` applique un traitement rigoureusement différencié à chacune des phases du chapitre 13 du manuel officiel Nobis :

### 1.1 Matrice de Traitement des Phases

| Phase Opérationnelle (`ÉTAT POÊLE`) | État Mécanique Vis Sans Fin (`VIS S. FIN`) & Extracteur (`EXT. FUMEES`) | Traitement dans le Bilan de Consommation ($\text{kg/h}$) | Traitement dans le Tarage de Combustion (Offsets 70 & 71) |
| :--- | :--- | :--- | :--- |
| `MISE EN MARCHE` | Préchauffage bougie ; pré-charge séquencée (Offset 70 inactif) | Intégration exacte des tours réels de l'encodeur BLDC (`VIS S. FIN` / $850\text{ RPM}$) | Exclu à $100\,\%$ (foyer froid, combustion transitoire) |
| `ATTENTE FLAMME` | Vis arrêtée ($0\text{ RPM}$) ; allumage par air chaud sur bougie | Intégration exacte ($0\text{ RPM} \Rightarrow 0\text{ kg}$ consommé) | Exclu à $100\,\%$ |
| `PHASE FLAMME` | Reprise vis sans fin et réduction progressive du comburant | Intégration exacte des tours réels de l'encodeur BLDC (sans Offset 70) | Exclu à $100\,\%$ + garde-fou d'inertie thermique de $20\text{ min}$ après la fin de l'allumage |
| `TRAVAIL` ($P_1$ à $P_7$) | Vis asservie à `SET V.S.F.` ; Offset 70 et Offset 71 pleinement actifs | Intégré dans le compartiment du palier $k \in \{1 \dots 7\}$ avec couplage Offset 70 | Retenu uniquement après $20\text{ min}$ post-allumage et $15\text{ min}$ de stabilité sur le palier $k$ |
| `ÉCONOMIE` (`RIS` / **P0**) | Modulation minimale lorsque la température ambiante est atteinte | Intégré dans le compartiment `P0` ($0,860\text{ kg/h}$ ou prorata `SET V.S.F.`) | Exclu à $100\,\%$ pour éviter le biais d'inertie thermique lors du repli à bas régime |
| `POWERFUL` (`PWF` / **P8**) | Ventilation forcée au maximum pendant $5\text{ min}$ ou sur plage `CHRONO` | Désactivé par l'assistant (`OFF`). Si forcé : intégré au prorata `SET V.S.F.` / $P_7$ | Exclu à $100\,\%$ pendant l'activation + $5\text{ min}$ de post-ventilation |
| `NETTOYAGE BRASERO` | Nettoyage périodique en marche (soufflerie fumées poussée) | Intégré normalement au palier de travail en cours | Exclu pendant toute la durée du nettoyage (dépression `PRS` et `RPM` forcés) |
| `DISPOSITIF DE NETTOYAGE ACTIF` | Extinction autonome, raclage mécanique du brasero puis `RALLUMAGE` | Compteurs suspendus pendant l'arrêt ; rallumage comptabilisé via l'encodeur RPM | Exclu à $100\,\%$ + réarmement du garde-fou d'inertie de $20\text{ min}$ au rallumage |
| `NETTOYAGE FINAL` / `ÉTEINT` / `ECO` | Vis coupée ($0\text{ RPM}$) ; extracteur actif pour refroidir l'échangeur | Exclu à $100\,\%$ (aucun granulé consommé) | Exclu à $100\,\%$ (chute de `T. FLAMME` et `T. HOT FUM` liée à l'extinction) |
| **Famine de fin de cuve** (Pré-`AL-03`) | Poêle encore affiché en `TRAVAIL`, mais vis tournant à vide (`AMP. V.S.F. < 65 mA`) | Arrêt immédiat du chronomètre dès que `AMP. V.S.F. < 65 mA` et $\frac{dT_{\text{flamme}}}{dt} < -15\,^\circ\text{C/min}$ | Purge rétroactive automatique des $10\text{ dernières minutes}$ du tampon de combustion dès l'apparition de `AL-03` |

---

## 2. Modèle Mathématique Consolidé

### 2.1 Bilan Gravimétrique Exact

À la détection obligatoire de l'alarme finale **AL-03** (`GRANULÉS ÉPUISÉS`), la masse théorique de référence $M_{\text{th, réf}}$ est calculée en sommant la masse exacte consommée durant les $N_{\text{allum}}$ séquences d'allumage (mesurée par l'encodeur tachymétrique $\text{RPM}_{\text{réel}}(t)$ de la vis sans fin BLDC $24\text{ V}$) et la masse consommée durant les phases actives $k \in \{0, 1, \dots, 8\}$ hors famine de fin de cuve :

$$M_{\text{th, réf}} = \underbrace{\dot{m}_{\text{nom}, 7} \int_{\text{Allumages}} \frac{\text{VIS S. FIN}_{\text{réel}}(t)}{\text{SET V.S.F.}_{\text{réf}, 7}} \, \frac{dt}{3600}}_{\text{Masse d'allumage mesurée par encodeur BLDC}} + \sum_{k=0}^{8} \dot{m}_{\text{nom}, k} \sum_{i \, \in \, \text{Palier } k}^{*} \alpha(i) \cdot \left(1 + \frac{\Delta_{70}(i)}{100}\right) \cdot \frac{\delta t_i}{3600}$$

où l'astérisque $\sum^{*}$ indique que les pas de temps $\delta t_i$ présentant une signature de famine pré-`AL-03` ($\text{AMP. V.S.F.} < 65\text{ mA}$ et $\frac{d(\text{T. FLAMME})}{dt} < -15\,^\circ\text{C/min}$) sont exclus de la somme.

### 2.2 Facteur Intrinsèque et Couplage Bidirectionnel

Le facteur intrinsèque du granulé $K_{\text{intrinsèque}}$ et le débit horaire effectif couplé $\dot{m}_{\text{eff}, k}(\Delta_{70})$ pour tout réglage d'Offset 70 $\Delta_{70} \in [-5, +5]$ sont alors donnés par :

$$K_{\text{intrinsèque}} = \frac{M_{\text{étalon}} - M_{\text{amorçage\_aspirée}}}{M_{\text{th, réf}}} \qquad \text{et} \qquad \dot{m}_{\text{eff}, k}(\Delta_{70}) = \dot{m}_{\text{nom}, k} \cdot K_{\text{intrinsèque}} \cdot \left(1 + \frac{\Delta_{70}}{100}\right)$$

### 2.3 Débits Nominaux Certifiés (IMQ CS25-0117813-01)

| Palier de Puissance | Puissance Brute / Utile (kW) | Débit Nominal $\dot{m}_{\text{nom}}$ (kg/h) | Consigne Consommation SET V.S.F. (RPM) | T° Fumées de Référence (°C) |
| :---: | :---: | :---: | :---: | :---: |
| **P0 (Économie / RIS)** | Régulation basse | 0,860 (ou ratio RPM) | Variable (< P1) | — |
| **P1** | 4,1 / 3,8 | 0,860 | 338 | 101,0 |
| **P2** | 5,1 / 4,8 | 1,077 | 423 | 112,2 |
| **P3** | 6,2 / 5,7 | 1,293 | 508 | 123,3 |
| **P4** | 7,2 / 6,7 | 1,510 | 594 | 134,5 |
| **P5** | 8,3 / 7,6 | 1,727 | 679 | 145,7 |
| **P6** | 9,3 / 8,6 | 1,943 | 765 | 156,8 |
| **P7** | 10,4 / 9,5 | 2,160 | 850 | 168,0 |
| **P8 (Powerful Boost)** | Boost forcé max | 2,320 (extrapolé) | 913 | — |

---

## 3. Recommandations de Tarage Combustion (Offsets 70 et 71)

Les écarts thermiques et de tirage sont analysés uniquement sur les fenêtres stabilisées $P_1 \dots P_7$ (après 20 min d'allumage, 15 min de palier, hors nettoyage, et hors famine) :

$$\Delta T_{\text{fumées}} = \frac{1}{N} \sum_{i=1}^{N} \left( T_{\text{fumées}}(i) - T_{\text{réf}}(P(i)) \right)$$

$$\varepsilon_{\text{tirage}} = \frac{1}{N} \sum_{i=1}^{N} \frac{\text{PRS}(i) - \text{SET PRS}(i)}{\text{SET PRS}(i)} \cdot 100\,\%$$

Les nouvelles valeurs d'Offset recommandées sont calculées par régulation proportionnelle :

$$\Delta_{70, \text{rec}} = \text{clamp}_{-5}^{+5}\left( \Delta_{70, \text{actuel}} - 0{,}65 \cdot (K - 1) \cdot 100 - 0{,}08 \cdot \Delta T_{\text{fumées}} - P_{\text{surcharge}} \right)$$

$$\Delta_{71, \text{rec}} = \text{clamp}_{-5}^{+5}\left( \Delta_{71, \text{actuel}} - 0{,}50 \cdot \varepsilon_{\text{tirage}} + 0{,}10 \cdot \Delta T_{\text{fumées}} + 0{,}35 \cdot \left((K - 1) \cdot 100 + \Delta_{70, \text{rec}} - \Delta_{70, \text{actuel}}\right) \right)$$

où $P_{\text{surcharge}} = 2{,}0$ si une surchauffe fumées ou un excès de combustible a été signalé par le firmware.

---

## 4. Protocole Opératoire en 5 Étapes

1. **Étape 1 : Pré-contrôle & Désactivation POWERFUL (P8)**
   L'assistant s'assure que le mode Boost `POWERFUL` est bien sur `OFF` (désactivation automatique via l'API Agua IoT `enable_pwf_set=0`).
2. **Étape 2 : Confirmation Cuve 100% Vide (`AL-03`)**
   L'étalonnage requiert de démarrer cuve entièrement vidée (confirmée par l'alarme `AL-03`). Réarmement de l'alarme pour revenir à l'état `ÉTEINT`.
3. **Étape 3 : Pesée du Sac Étalon & Amorçage**
   Pesée exacte d'un sac neuf de granulés (ex: 15,000 kg). Remplissage de la trémie. Si la fonction `CRG. VIS S. FIN` (210 s) est utilisée pour réamorcer la vis, les granulés tombés dans le brasero sont soit remis dans la trémie, soit déduits de la masse initiale nette via `aspirated_priming_grams`.
4. **Étape 4 : Scrutation Télémétrique & Acquisition**
   Le poêle fonctionne normalement sur sa programmation habituelle. L'assistant intègre les tours RPM réels à l'allumage, filtre l'inertie de chauffe (20 min) et de palier (15 min), suspend les calculs pendant les nettoyages et détecte la famine en fin de cuve.
5. **Étape 5 : Clôture Figée sur `AL-03` & Application en 1 Clic**
   Dès la réapparition de `AL-03`, les compteurs sont gelés, les 10 dernières minutes sont rétroactivement purgées du tampon combustion, et les recommandations d'Offsets 70/71 sont générées avec application en 1 clic dans l'EEPROM Micronova et vérification Read-After-Write.
