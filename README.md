# 🔥 Nobis A10 Supervisor & Matter Bridge

> **Cockpit de supervision haute fidélité, télémétrie temps réel et passerelle domotique Matter locale pour poêle à granulés Nobis A10 C Light Style Coax (électronique Micronova).**

[![Python Version](https://img.shields.io/badge/Python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com/)
[![Node.js](https://img.shields.io/badge/Node.js-20%2B-green.svg)](https://nodejs.org/)
[![Matter Ready](https://img.shields.io/badge/Matter-1.2%2B-orange.svg)](https://csa-iot.org/all-solutions/matter/)
[![Docker Ready](https://img.shields.io/badge/Docker-Compose-2496ED.svg)](https://www.docker.com/)
[![PWA Ready](https://img.shields.io/badge/PWA-Installable-5A0FC8.svg)](https://web.dev/progressive-web-apps/)
[![Tests](https://img.shields.io/badge/Tests-22%20passing-brightgreen.svg)]()

---

## 📖 Sommaire

- [Aperçu & Objectifs](#-aperçu--objectifs)
- [Fonctionnalités Principales](#-fonctionnalités-principales)
  - [Pilotage Thermique & Ventilation](#-pilotage-thermique--ventilation)
  - [Mode Silence ! Temporisé](#-mode-silence--temporisé)
  - [Chrono Hebdomadaire (P1 à P4)](#-chrono-hebdomadaire-p1-à-p4)
  - [Gestion de Trémie & Autonomie Météo](#-gestion-de-trémie--autonomie-météo)
  - [Diagnostics Avancés & Maintenance Prédictive](#-diagnostics-avancés--maintenance-prédictive)
  - [Pont Domotique Matter Local](#-pont-domotique-matter-local)
  - [Sauvegarde, Audit & Snapshots](#-sauvegarde-audit--snapshots)
- [Modes de Communication](#-modes-de-communication)
- [Architecture Technique](#-architecture-technique)
- [Arborescence du Projet](#-arborescence-du-projet)
- [Installation & Démarrage](#-installation--démarrage)
  - [Option 1 : Déploiement Docker (Recommandé)](#option-1--déploiement-docker-recommandé)
  - [Option 2 : Installation Manuelle (Local / Développement)](#option-2--installation-manuelle-local--développement)
- [Configuration](#-configuration)
- [Intégration Matter (Google Home, Apple Home, Alexa)](#-intégration-matter)
- [API REST & Flux SSE](#-api-rest--flux-sse)
- [Tests Unitaires](#-tests-unitaires)
- [Dépannage & FAQ](#-dépannage--faq)
- [Avertissement & Licence](#-avertissement--licence)

---

## 🎯 Aperçu & Objectifs

Le poêle à pellets étanche et coaxial **Nobis A10 C Light Style Coax** est un équipement de chauffage haut de gamme piloté par une carte électronique **Micronova**. Bien que performant, son utilisation au quotidien via les solutions mobiles d'origine souffre souvent de dépendances au cloud constructeur, de latences et d'un manque d'intégration avec les écosystèmes domotiques modernes.

**Nobis A10 Supervisor** apporte une réponse complète et autonome :
1. **Souveraineté locale** : fonctionnement 100% autonome en local (via passerelle locale ou jumeau numérique) avec fallback cloud Micronova si désiré.
2. **Interface PWA moderne** : cockpit web réactif, installable sur smartphone/tablette/PC (icône dédiée, mode plein écran, thème sombre soigné avec effet verre dépoli *glassmorphism*).
3. **Passerelle Matter native** : appairage direct du poêle en tant que thermostat Matter avec Google Home, Nest Hub Max, Apple Home / Siri, Alexa et Home Assistant, sans passer par un cloud externe.
4. **Précision & Télémétrie** : suivi en temps réel par Server-Sent Events (SSE) de tous les registres RAM/EEPROM, calcul dynamique de l'autonomie en granulés corrélé aux prévisions météo locales (Open-Meteo), indices d'efficacité de combustion et suivi d'usure CAT.

---

## ✨ Fonctionnalités Principales

### 🎛️ Pilotage Thermique & Ventilation
- **Marche / Arrêt sécurisé** avec gestion des phases de transition (Préchauffage, Allumage, Travail, Nettoyage, Arrêt/Refroidissement).
- **Consigne de température ambiante** réglable de 7 à 40°C (ou 41°C = Mode Manuel continu).
- **Puissance de combustion** échelonnée de P1 à P7.
- **Ventilation frontale** réglable de 0 (OFF) à 7, ou 8 (*PS Auto* proportionnel à la puissance de chauffe).
- **Canalisations d'air indépendantes** : gestion séparée de la canalisation **Gauche (SX)** et de la canalisation **Droite (DX)** (consigne de température de 7 à 40°C et vitesse de soufflerie de 0 à 8).
- **Modes de combustion automatiques** :
  - **Modulation Auto** : ajustement dynamique de la puissance selon le différentiel de température.
  - **Mode ECO (Stand-by)** : coupure et réallumage automatiques dès que la température de consigne est atteinte.
  - **Mode BOOST (PWF)** : surpuissance temporaire pour réchauffer rapidement la pièce.

### 🤫 Mode Silence ! Temporisé
Une exclusivité conçue pour les moments de repos ou de lecture :
- **Activation en 1 clic** : bascule immédiatement le poêle en **convection naturelle 100% silencieuse**.
- Bride la puissance sur **P1** et coupe l'intégralité des ventilateurs (Frontale, Canalisations SX et DX à 0).
- Désactive temporairement les modes Auto, ECO et BOOST.
- **Temporisation réglable** : présélections rapides 1h, **2h (défaut)**, 3h, 4h ou saisie personnalisée en minutes/heures.
- Compte à rebours dynamique affiché sur le bouton de commande.
- **Restauration automatique** : à la fin du timer, les réglages initiaux de puissance et de ventilation sont automatiquement rétablis.

### 📅 Chrono Hebdomadaire (P1 à P4)
- **4 plages programmables indépendantes** (P1, P2, P3, P4).
- Sélection des jours actifs via masques hebdomadaires (Lundi au Dimanche).
- Définition pour chaque programme de :
  - L'heure de démarrage et d'extinction (au pas de 10 minutes).
  - La puissance de consigne (P1 à P7).
  - La température ambiante souhaitée.
  - La température et vitesse des canalisations Gauche et Droite.
  - La vitesse du ventilateur d'ambiance frontal.
- **Synchronisation d'horloge NTP en 1 clic** : met à l'heure la date et l'heure internes de la carte mère du poêle avec l'horloge système.

### 🪵 Gestion de Trémie & Autonomie Météo
- Suivi du niveau de granulés dans le réservoir de **30 kg** (affichage en pourcentage et en kilogrammes).
- Bouton de remplissage rapide **"+ Sac 15 kg"** avec plafonnement automatique à 30 kg.
- Consommation calculée en continu d'après le temps d'activation de la vis sans fin d'alimentation (`auger_duty_pct`).
- **Prévisions météo locales intégrées** (API Open-Meteo) : température extérieure, humidité, vent et historique récent.
- **Calcul prévisionnel de l'autonomie restante (Runway en jours)** basé sur les moyennes thermiques extérieures prévues pour les prochains jours.

### 🔍 Diagnostics Avancés & Maintenance Prédictive
- **Indice de qualité des granulés** : compare en temps réel la température de la sonde de flamme K à la courbe théorique attendue selon la puissance sélectionnée (pastille colorée : optimale, acceptable ou dégradée).
- **Indice d'extraction & tirage** : surveille la dépression du foyer en Pascal (Pa), le régime de l'extracteur de fumées (RPM) et la température des gaz brûlés.
- **Tarages installateur (Trims)** : ajustement fin du chargement granulés (-30% à +30%) et de la vitesse de l'extracteur de fumées (-30% à +30%).
- **Compteur de maintenance CAT** : décompte des heures de fonctionnement avant l'entretien obligatoire des 2500 heures.
- **Gestion des alarmes Micronova** : détection et libellé explicite des 29 codes d'alarme de sécurité (AL01 Black-out, AL02 Manque allumage, AL03 Manque pellets, AL11 Dépression tirage, AL15 Sécurité porte/cendres, etc.) avec bouton de réinitialisation.

### 🌐 Pont Domotique Matter Local
- Embarque un pont Matter autonome écrit en Node.js moderne avec `@matter/main`.
- Expose le poêle sur le réseau local en tant qu'accessoire **Thermostat standard**.
- Compatible avec les contrôleurs du marché :
  - **Google Home** & **Nest Hub / Nest Hub Max**
  - **Apple HomeKit**, **Apple TV**, **HomePod** (avec commande vocale Siri)
  - **Amazon Alexa**
  - **Home Assistant** (intégration Matter officielle)
- Affichage direct du **QR Code d'appairage** SVG et du code manuel dans l'onglet Installateur.
- Synchronisation bidirectionnelle : une consigne modifiée sur Google Home ou Apple Home met à jour le poêle, et un changement manuel sur le poêle remonte aussitôt sur les applications domotiques.

### 💾 Sauvegarde, Audit & Snapshots
- **Snapshots de registres** : mémorisez l'état complet de vos registres EEPROM/RAM avant modification et restaurez-les à tout moment.
- **Audit des commandes & Rollback** : chaque ordre envoyé au poêle est consigné dans une base SQLite. Possibilité de faire un retour arrière (*Rollback*) sur n'importe quel ordre unitaire.
- **Export CSV** : téléchargez en un clic l'historique complet de télémétrie ou les logs pour une analyse sous Excel / Grafana.
- **Webhooks & Alertes Push** : notifications instantanées (Discord, Slack, ntfy.sh, Home Assistant) lors d'un défaut, d'une alarme ou d'un niveau bas de trémie (protection anti-SSRF incluse).

---

## 📡 Modes de Communication

L'application prend en charge 3 modes de communication interchangeables à chaud :

| Mode | Description | Cas d'usage |
| :--- | :--- | :--- |
| **`simulation`** | **Jumeau numérique physique haute fidélité** : simule la thermique du poêle, la montée en flamme, les dépressions, les alarmes et la consommation de combustible sans matériel physique. | Développement, démonstration, tests hors saison de chauffe. |
| **`agua_cloud`** | **Client Cloud Micronova Agua IOT** : communique avec l'API officielle Micronova par jeton JWT et protocole `RWMSmaster`. | Pilotage à distance sans modification matérielle de l'installation. |
| **`local_gateway`** | **Passerelle locale UART/RS485/Modbus** : interroge directement une passerelle matérielle connectée sur le port série de la carte mère. | Zéro latence, zéro cloud, résilience totale aux pannes Internet. |

---

## 🏗️ Architecture Technique

```mermaid
flowchart TD
    subgraph Clients["📱 Écosystème Utilisateur"]
        Browser["PWA (Navigateur / Mobile / Tablette)\nTailwind CSS • Chart.js • SSE"]
        SmartHome["Domotique Matter\nGoogle Home • Apple Home • Alexa • Home Assistant"]
    end

    subgraph Core["⚙️ Nobis A10 Supervisor (FastAPI)"]
        API["FastAPI REST & SSE Router\n(/api/latest, /api/command, /api/events)"]
        Scheduler["APScheduler\n(Boucle télémétrie 5s-30s • Purge SQLite)"]
        TelemetryEngine["Moteur d'Analyse Télémétrique\n(Autonomie météo, Qualité flamme K, Tirage Pa)"]
        DB[(SQLite WAL\nSystème, Audit, Snapshots, Télémétrie)]
    end

    subgraph MatterBridge["🌐 Pont Matter (@matter/main)"]
        NodeBridge["Serveur Node.js (Port 5540)\nCluster Thermostat • QR Code Commissioning"]
    end

    subgraph Harvesters["🔌 Interfaces Matérielles"]
        Sim["Jumeau Numérique (Simulation)"]
        AguaCloud["Cloud Micronova Agua IOT"]
        LocalGW["Passerelle Locale UART/RS485"]
    end

    Browser <-->|HTTP REST & Flux SSE temps réel| API
    SmartHome <-->|Protocole Matter IPv6/UDP 5540| NodeBridge
    NodeBridge <-->|Webhook local & Sync JSON| API
    API <--> DB
    Scheduler --> TelemetryEngine
    TelemetryEngine <--> Harvesters
    TelemetryEngine --> DB
```

---

## 📂 Arborescence du Projet

```text
Nobis_App/
├── docker-compose.yml          # Déploiement multi-conteneurs prêt à l'emploi
├── Dockerfile                  # Image Docker basée sur python:3.11-slim + Node.js
├── requirements.txt            # Dépendances Python (FastAPI, Uvicorn, APScheduler, Pydantic, HTTPX)
├── .gitignore                  # Exclusion des bases SQLite locales, venv, caches
├── README.md                   # Documentation complète du projet
├── data/                       # Données persistantes montées en volume (bases SQLite, Matter)
│   ├── nobis_a10_system.db     # Configuration, audits, snapshots, réglages
│   ├── nobis_a10_*.db          # Historique télémétrique selon le mode actif
│   └── matter_storage/         # Clés cryptographiques et fabrications Matter
└── app/
    ├── __init__.py
    ├── main.py                 # Serveur FastAPI, cycle de vie, endpoints REST et flux SSE
    ├── config.py               # Constantes poêle (alarmes AL01-AL29, registres EEPROM/RAM)
    ├── database.py             # Gestion SQLite en mode WAL, migrations, snapshots et audits
    ├── clients.py              # Clients HTTPX (Agua IOT Cloud, Passerelle Locale, Open-Meteo)
    ├── schemas.py              # Modèles de validation des données Pydantic v2
    ├── telemetry.py            # Algorithmes thermodynamiques, calculs d'autonomie et débits
    ├── matter_service.py       # Superviseur Python du sous-processus Node.js Matter Bridge
    ├── matter_bridge/          # Pont Matter autonome en Node.js
    │   ├── package.json        # Dépendances @matter/main et qrcode
    │   └── bridge.mjs          # Serveur de cluster Thermostat Matter
    ├── static/                 # Frontend PWA (Single Page Application)
    │   ├── index.html          # Dashboard, panneaux de contrôle, onglets Chrono/Diagnostic
    │   ├── manifest.json       # Configuration PWA (icônes, couleur de thème, affichage standalone)
    │   ├── sw.js               # Service Worker pour le cache et fonctionnement hors-ligne
    │   ├── css/styles.css      # Thème sombre personnalisé, animations feu/ventilation
    │   └── js/
    │       ├── app.js          # Contrôleur d'interface, flux SSE, gestion des modales
    │       ├── state.js        # Gestion d'état réactive côté client
    │       └── charts.js       # Rendu des graphiques historiques Chart.js
    └── tests/                  # Suite de tests automatisés
        ├── test_analytics.py   # Validation des algorithmes de qualité et d'autonomie
        ├── test_api.py         # Tests d'intégration des routes REST et de sécurité
        └── test_schemas.py     # Validation des schémas Pydantic
```

---

## 🚀 Installation & Démarrage

### Option 1 : Déploiement Docker (Recommandé)

Le déploiement sous Docker est la solution la plus simple et la plus fiable. L'image embarque à la fois l'environnement Python 3.11 et le runtime Node.js nécessaire au pont Matter.

1. **Cloner le dépôt Git :**
   ```bash
   git clone https://github.com/votre-compte/nobis-a10-supervisor.git
   cd nobis-a10-supervisor
   ```

2. **Lancer le conteneur avec Docker Compose :**
   ```bash
   docker compose up -d
   ```

3. **Accéder à l'application :**
   - Ouvrez votre navigateur sur : **`http://localhost:8080`** (ou l'adresse IP de votre machine/Raspberry Pi).
   - Les données et états Matter sont automatiquement persistés dans le répertoire `./data`.

4. **Consulter les journaux d'exécution :**
   ```bash
   docker compose logs -f nobis-supervisor
   ```

---

### Option 2 : Installation Manuelle (Local / Développement)

#### Prérequis
- **Python 3.11** ou **Python 3.12**
- **Node.js 20+** et **npm** (pour le pont Matter)

#### Étapes

1. **Cloner le dépôt :**
   ```bash
   git clone https://github.com/votre-compte/nobis-a10-supervisor.git
   cd nobis-a10-supervisor
   ```

2. **Configurer l'environnement virtuel Python :**
   ```bash
   # Sous Linux / macOS
   python3 -m venv .venv
   source .venv/bin/activate

   # Sous Windows PowerShell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1

   # Installation des dépendances
   pip install -r requirements.txt
   ```

3. **Installer les dépendances du pont Matter :**
   ```bash
   cd app/matter_bridge
   npm install --omit=dev
   cd ../..
   ```

4. **Démarrer le superviseur :**
   ```bash
   # Sous Linux / macOS
   uvicorn app.main:app --host 0.0.0.0 --port 8080

   # Sous Windows PowerShell
   $env:PYTHONPATH="."
   .\.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8080
   ```

L'application initialise automatiquement les bases de données SQLite nécessaires dans `data/` et démarre en mode `simulation` par défaut.

---

## ⚙️ Configuration

Toutes les configurations principales peuvent être ajustées directement depuis l'onglet **"Installateur"** de l'interface web, ou via des variables d'environnement.

### Variables d'Environnement Disponibles

| Variable | Valeur par défaut | Description |
| :--- | :--- | :--- |
| `DB_PATH` | `/app/data/nobis_a10.db` | Chemin du dossier des bases SQLite |
| `TZ` | `Europe/Paris` | Fuseau horaire pour l'horodatage des relevés |
| `NOBIS_API_URL` | `http://127.0.0.1:8080` | URL FastAPI utilisée par le pont Matter |
| `MATTER_PORT` | `5540` | Port réseau UDP/TCP pour le protocole Matter |
| `MATTER_CONTROL_PORT`| `5549` | Port de contrôle interne du pont Matter |
| `MATTER_PASSCODE` | `20202021` | Code secret d'appairage Matter |
| `MATTER_DISCRIMINATOR`| `3840` | Discriminateur d'appairage Matter |

---

## 📱 Intégration Matter

Le pont Matter intégré expose votre poêle sous forme d'un **Thermostat** :

```text
Type d'appareil : Thermostat (Device ID: 0x0301)
Nom par défaut  : Poêle Nobis A10
Passcode        : 20202021
Discriminator   : 3840
Port Matter     : 5540 (UDP / TCP)
```

### Procédure d'Appairage

1. Dans l'interface web, rendez-vous dans l'onglet **🔧 Installateur** > section **Pont Matter**.
2. Vérifiez que le statut indique **"En ligne / Opérationnel"**.
3. Sur votre smartphone (sur le même réseau Wi-Fi local avec Bluetooth activé) :
   - **Google Home** : Appuyez sur `+` > *Configurer un appareil* > *Appareil compatible avec Matter* > Scannez le QR Code affiché à l'écran.
   - **Apple Home** : Ouvrez Maison > `+` > *Ajouter un accessoire* > Scannez le QR Code.
   - **Alexa / Home Assistant** : Choisissez l'ajout d'appareil Matter et suivez les instructions.
4. Une fois appairé, vous pouvez piloter votre poêle à la voix :
   - *"Dis Google, règle le poêle à 20 degrés."*
   - *"Dis Siri, éteins le poêle."*

---

## 🔌 API REST & Flux SSE

Le superviseur met à disposition une API REST documentée accessible sur `/docs` (Swagger UI).

### Endpoints Principaux

| Méthode | Route | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Bilan de santé du service, statut SQLite et mode actif |
| `GET` | `/api/events` | Flux Server-Sent Events (SSE) temps réel (données poêle, météo, alertes) |
| `GET` | `/api/latest` | Dernier enregistrement complet de télémétrie et registres |
| `GET` | `/api/dashboard?range_key=24h` | Historique agrégé pour les courbes graphiques (1h, 6h, 12h, 24h, 7j, 30j) |
| `POST` | `/api/command` | Écriture d'un registre (`reg_key`, `new_val`) |
| `POST` | `/api/command-batch` | Écriture groupée de plusieurs registres en une seule requête |
| `POST` | `/api/silence/activate` | Activation du mode Silence ! avec durée en heures |
| `POST` | `/api/silence/deactivate` | Fin anticipée du mode Silence ! et restauration des paramètres |
| `GET` | `/api/silence/status` | Statut du mode Silence et compte à rebours restant |
| `GET` | `/api/matter/status` | Statut du pont Matter, QR Code d'appairage et code manuel |
| `POST` | `/api/matter/restart` | Redémarrage du sous-processus pont Matter |
| `POST` | `/api/matter/factory-reset`| Réinitialisation d'usine des appairages Matter |
| `POST` | `/api/refill-hopper` | Ajout de granulés dans la trémie (ex: +15 kg) |
| `POST` | `/api/sync-clock` | Synchronisation de l'horloge interne du poêle avec l'heure NTP |
| `GET` | `/api/snapshots` | Liste des sauvegardes de configuration |
| `POST` | `/api/snapshots` | Création d'un instantané de tous les registres |
| `POST` | `/api/snapshots/{id}/restore`| Restauration intégrale d'un instantané |
| `POST` | `/api/rollback/{audit_id}`| Annulation ciblée d'un ordre d'écriture antérieur |
| `GET` | `/api/export-csv` | Téléchargement des relevés télémétriques au format CSV |

---

## 🧪 Tests Unitaires

Une suite complète de 22 tests automatisés valide la cohérence des schémas Pydantic, les calculs thermodynamiques et la sécurité des routes HTTP :

```bash
# Exécution avec unittest
python -m unittest discover -s tests

# Ou avec pytest (si installé)
pytest
```

```text
......................
----------------------------------------------------------------------
Ran 22 tests in ~7s

OK
```

---

## 🛠️ Dépannage & FAQ

<details>
<summary><b>1. Le pont Matter n'apparaît pas ou le QR Code ne scanne pas</b></summary>

- Assurez-vous que le port UDP **5540** n'est pas bloqué par un pare-feu local (nécessaire pour la découverte mDNS Matter).
- Si vous utilisez Docker, vérifiez que le mapping de port `5540:5540/udp` et `5540:5540/tcp` est bien présent dans votre `docker-compose.yml`.
- Dans l'onglet Installateur, vous pouvez déclencher une **Réinitialisation d'usine Matter** pour régénérer un identifiant d'appairage propre.
</details>

<details>
<summary><b>2. Comment passer du mode Simulation au poêle réel ?</b></summary>

1. Rendez-vous dans l'onglet **🔧 Installateur**.
2. Dans le bloc **Mode de communication**, sélectionnez :
   - **Agua Cloud** : renseignez votre email, mot de passe Agua IOT et identifiant client.
   - **Passerelle Locale** : renseignez l'adresse IP locale de votre passerelle (ex: `http://192.168.1.150`).
3. Cliquez sur **Enregistrer les paramètres**. Le superviseur bascule immédiatement sur votre installation physique.
</details>

<details>
<summary><b>3. Comment installer la PWA sur iPhone / Android ?</b></summary>

- **Sur iPhone / iPad (Safari)** : appuyez sur le bouton *Partager* (carré avec flèche vers le haut) > sélectionnez **Sur l'écran d'accueil**.
- **Sur Android (Chrome)** : appuyez sur le menu `⋮` > sélectionnez **Ajouter à l'écran d'accueil** ou **Installer l'application**.
- L'application se comporte alors comme une application native sans barre d'adresse ni boutons de navigation du navigateur.
</details>

---

## ⚖️ Avertissement & Licence

- **Avertissement** : Ce projet est un développement communautaire indépendant et n'est ni affilié à, ni approuvé par la société **Nobis Srl** ou **Micronova Srl**. L'utilisation des commandes installateur et des réglages de registres relève de la responsabilité de l'utilisateur.
- **Licence** : Distribué sous licence open-source **MIT**. Vous êtes libre de l'utiliser, le modifier et le distribuer.

---

<p align="center">
  Fait avec passion pour le confort thermique et la domotique ouverte 🏡🔥
</p>
