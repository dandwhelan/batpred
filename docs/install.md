# Install

These instructions will take you through the process of installing and configuring Predbat for first time use.

It's recommended that you watch the [Video Guides](video-guides.md) before you start.

A level of familiarity with the basics of Home Assistant, Add-on's, Integrations, Entities and File Editing is assumed.
There are plenty of "Home Assistant basics" tutorials on YouTube, but if you get stuck, please read the [FAQ's](faq.md)
and if necessary raise a [Github ticket](https://github.com/springfall2008/batpred/issues) for support.

## Inverter Control Integration install (GivTCP/SolaX-ModBus)

The Integration that communicates with your inverter will be depend on the brand of inverter you have:

| Brand     | Integration  | Github Link                                                                      |
| :-------- | :----------- | :------------------------------------------------------------------------------- |
| GivEnergy | GivTCP       | [https://github.com/britkat1980/giv_tcp](https://github.com/britkat1980/giv_tcp) |
| Solis     | SolaX ModBus | <https://github.com/wills106/homeassistant-solax-modbus>                         |
| Sofar     | Sofar MQTT   | <https://github.com/cmcgerty/Sofar2mqtt> |

Please see [Other Inverters](other-inverters.md) for details on how Solis, Solax and Sofar install details.

- Follow the installation and configuration instructions appropriate for your inverter so that Home Assistant is able to 'see' and manage your inverter.
- You will need at least 24 hours history in Home Assistant for Predbat to work correctly, the default is 7 days (but you configure this back to 1 day if you need to).

## Editing Configuration Files in Home Assistant

The basic configuration for Predbat is stored in a configuration file called `apps.yaml`.
A standard template apps.yaml file will be installed as part of the Predbat installation and you will need to edit and customise this configuration file for your own system setup.

You will therefore need a method of editing configuration files within your Home Assistant environment.

There are severals ways to achieve this in Home Assistant, but two of the simplest are to use either the File Editor or Studio Code Server add-on's.
Whichever you use is a personal preference. File Editor is a bit simpler, Studio Code Server is more powerful
but does require HACS (the Home Assistant Community Store) to be installed first.

If you do not have one of these file editors already installed in Home Assistant:

- For Studio Code Server you will need to [install HACS](#hacs-install) first if you don't currently have it installed
- Go to Settings / Add-ons / Add-on Store (bottom right)
- Scroll down the add-on store list, to find either 'File editor' or 'Studio Code Server' as appropriate, click on the add-on, click 'INSTALL'
- Once the editor has been installed, ensure that the 'Start on boot' option is turned on, and click 'START' to start the add-on

Thereafter whenever you need to edit a configuration file in Home Assistant you can navigate to Settings / Add-on's / <editor_you_chose_to_use> / 'OPEN WEB UI'

If you are using the File Editor to edit Predbat's configuration files, you will need to turn off **Enforce Basepath** to enable you to access files in the appdaemon directory:

- From the File editor add-on page, click on the 'Configuration' tab to change this setting):<BR>
![image](https://github.com/springfall2008/batpred/assets/48591903/298c7a19-3be9-43d6-9f1b-b46467701ca7)

## AppDaemon-Predbat combined install

**Recommended**

The simplest way to install Predbat now is with a combined AppDaemon/Predbat add-on.
This is a fork of AppDaemon which automatically includes an install of Predbat.

Installing the combined AppDaemon-predbat add-on is thus simpler for new users as they do not need to install HACS, AppDaemon and Predbat as three separate installation steps.
If you are already running AppDaemon then the original installation method for Predbat still exists, is still supported, and is described below in [Predbat Installation into AppDaemon](#predbat-installation-into-appdaemon).

To install the combined AppDaemon-predbat add-on:

- Go to Settings / Add-ons / Add-on Store (bottom right), click the three dots in the top right, then Repositories and type
[https://github.com/springfall2008/appdaemon-predbat](https://github.com/springfall2008/appdaemon-predbat)', click ADD, then CLOSE.
- In order to refresh the list of available add-on's, navigate back through Settings / Add-ons / Add-on Store, scroll down and select 'AppDaemon with Predbat'
- Click INSTALL and wait for the add-on to be installed
- Once it has finished installing, ensure that the 'Start on boot' option is turned on, then click 'START'

**NOTE:** Throughout the rest of the Predbat documentation you will find reference to the Predbat configuration file `apps.yaml` and the Predbat logfile.

These are located under the Home Assistant directory `/addon_configs/46f69597_appdaemon-predbat` which contains:

- **predbat.log** - Predbat's active logfile that reports detail of what Predbat is doing, and details of any errors
- **apps/apps.yaml** - Predbat's configuration file which will need to be customised to your system and requirements. This configuration process is described below.

You can use your file editor (i.e. 'File editor' or 'Studio Code Server' add-on) to open the directory `/addon_configs/46f69597_appdaemon-predbat` and view these files.

If you have used the AppDaemon-predbat add-on installation method you do not need to install HACS or AppDaemon so you can skip directly to [Solcast install](#solcast-install) below.

## Predbat installation into AppDaemon

This is the "classic" way of installing Predbat, to firstly install HACS (the Home Assistant Community Store), then install the AppDaemon add-on,
and finally install Predbat from HACS to run within AppDaemon.

### HACS install

Predbat and AppDaemon are available through the Home Assistant Community Store (HACS). You can install Predbat manually (see below) but its usually easier to install it through HACS.

- Install HACS if you haven't already ([https://hacs.xyz/docs/setup/download](https://hacs.xyz/docs/setup/download))
- Enable AppDaemon in HACS: [https://hacs.xyz/docs/categories/appdaemon_apps/](https://hacs.xyz/docs/categories/appdaemon_apps/)

### AppDaemon install

Predbat is written in Python and runs on a continual loop (default every 5 minutes) within the AppDaemon add-on to Home Assistant.
The next task therefore is to install and configure AppDaemon.

- Install the AppDaemon add-on [https://github.com/hassio-addons/addon-appdaemon](https://github.com/hassio-addons/addon-appdaemon)
- Once AppDaemon has finished installing, ensure that the 'Start on boot' option is turned on, then click 'START'
- You will need to edit the `appdaemon.yaml` configuration file for AppDaemon and so will need to have either
[the File Editor or Studio Code Server add-on's installed](#editing-configuration-files-in-home-assistant) first
- Find the `appdaemon.yaml` file in the directory `/addon_configs/a0d7b954_appdaemon`: ![image](https://github.com/springfall2008/batpred/assets/48591903/bf8bf9cf-75b1-4a8d-a1c5-fbb7b3b17521)
- Add to the `appdaemon.yaml` configuration file:
    - A section **app_dir** which should refer to the directory `/homeassistant/appdaemon/apps` where Predbat will be installed
    - Ensure that the **time_zone** is set correctly (e.g. Europe/London)
    - Add **thread_duration_warning_threshold: 120** in the appdaemon section
- It's recommended you also add a **logs** section and specify a new logfile location so that you can see the complete logs, I set mine
to `/homeassistant/appdaemon/appdaemon.log` and increase the logfile maximum size and number of logfile generations to capture a few days worth of logs.

Example AppDaemon config in `appdaemon.yaml`:

```yaml
appdaemon:
  latitude: 52.379189
  longitude: 4.899431
  elevation: 2
  time_zone: Europe/London
  thread_duration_warning_threshold: 120
  plugins:
    HASS:
      type: hass
  app_dir: /homeassistant/appdaemon/apps
http:
  url: http://homeassistant.local:5050
admin:
api:
hadashboard:

# write log records to a file, retaining 9 versions, rather than the standard appdaemon log
logs:
  main_log:
    filename: /homeassistant/appdaemon/appdaemon.log
    log_generations: 9
    log_size: 10000000
```

CAUTION: If you are upgrading AppDaemon from an older version to version 0.15.2 or above you need to follow these steps to ensure Predbat continues working.
These are only required if you are upgrading AppDaemon from an old version, they're not required for new installations of AppDaemon:

- Make sure you have access to the HA filesystem, e.g. I use the Samba add-on and connect to the drives on my Mac, but you can use ssh also.
- Update AppDaemon to the latest version
- Go into the directory `/addon_configs/a0d7b954_appdaemon` and edit `appdaemon.yaml`. You need to add app_dir (see above) to point to the
old location and update your logfile location (if you have set it). You should remove the line that points to secrets.yaml
(most people don't use this file) or adjust it's path to the new location (`/homeassistant/secrets.yaml`)
- Move the entire 'apps' directory from `/addon_configs/a0d7b954_appdaemon` (new location) to `/config/appdaemon` (the old location)
- Restart AppDaemon
- Check it has started and confirm Predbat is running correctly again.

### Install Predbat through HACS

[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)

If you install Predbat through HACS, once installed you will get automatic updates for each new release of Predbat!

- In HACS, click on Automation
- Click on the three dots in the top right corner, choose *Custom Repositories*
- Add <https://github.com/springfall2008/batpred> as a custom repository of Category 'AppDaemon' and click 'Add'
- Click *Explore and download repositories* (bottom right), type 'Predbat' in the search box, select the Predbat Repository, then click 'Download' to install the Predbat app.

**NOTE:** Throughout the rest of the Predbat documentation you will find reference to the Predbat configuration file `apps.yaml` and the Predbat logfile.

As you are following the 'install Predbat through HACS' installation method these are located under the Home Assistant directory `/config/appdaemon/` which contains:

- **appdaemon.log** - AppDaemon and Predbat's active logfile that reports detail of what Predbat is doing, and details of any errors
- **apps/batpred/config/apps.yaml** - Predbat's configuration file which will need to be customised to your system and requirements. This configuration process is described below.

## Predbat manual install

A manual install is suitable for those running Docker type systems where HACS does not function correctly and you had to manually install AppDaemon.

Note: **Not recommended if you are using HACS**

- Copy the file apps/predbat/predbat.py to the `/config/appdaemon/apps/` directory in Home Assistant (or wherever you set appdaemon app_dir to)
- Copy apps/predbat/apps.yaml to the `/config/appdaemon/apps/` directory in Home Assistant (or wherever you set appdaemon app_dir to)
- Edit in Home Assistant the `/config/appdaemon/apps/apps.yaml` file to configure Predbat

- If you later install with HACS then you must move the `apps.yaml` into `/config/appdaemon/apps/predbat/config`

## Solcast Install

Predbat needs a solar forecast in order to predict solar generation and battery charging.

If you don't have solar then use a file editor to comment out the following lines from the Solar forecast part of the `apps.yaml` configuration:

```yaml
  pv_forecast_today: re:(sensor.(solcast_|)(pv_forecast_|)forecast_today)
  pv_forecast_tomorrow: re:(sensor.(solcast_|)(pv_forecast_|)forecast_tomorrow)
  pv_forecast_d3: re:(sensor.(solcast_|)(pv_forecast_|)forecast_(day_3|d3))
  pv_forecast_d4: re:(sensor.(solcast_|)(pv_forecast_|)forecast_(day_4|d4))
```

If you do have solar panels its recommended to use the Solcast integration to automatically retrieve your forecast solar generation.
Predbat is configured to automatically discover the Solcast forecast entities in Home Assistant.

Install the Solcast integration (<https://github.com/oziee/ha-solcast-solar>), create a [Solcast account](https://solcast.com/),
configure details of your solar arrays, and request an API key that you enter into the Solcast integration in Home Assistant.

Note that Predbat does not update Solcast for you so you will need to create your own Home Assistant automation that updates the solar forecast a few times a day
(e.g. dawn, dusk, and just before your nightly charge slot).

Example Solcast update automation script:

```yaml
alias: Solcast update
description: "Update Solcast solar forecast"
trigger:
  - platform: time
    at: "23:00:00"
  - platform: time
    at: "12:00:00"
  - platform: time
    at: "04:00:00"
condition: []
action:
  - service: solcast_solar.update_forecasts
    data: {}
mode: single
```

Manually run the automation and then make sure the Solcast integration is working in Home Assistant by going to Developer Tools / States, filtering on 'solcast',
and checking that you can see the half-hourly solar forecasts in the Solcast entities.

## Energy Rates

Predbat needs to know what your electricity import and export rates are in order to optimise battery charging and discharging to minimise your expenditure.

These rates are configured in Predbat's `apps.yaml` configuration file. Follow the instructions in the [Energy Rates](energy-rates.md) document.

**Note:** that if you are using the Octopus integration the 'sensor.octopus_xxx' and 'event.octopus_xxx' entities must have a similar pattern of
names for Predbat to work correctly - see the [FAQ's](faq.md) if they are not.

## Configuring Predbat

You will need to use a file editor (either the File editor or Studio Code Server add-on) to edit the `apps.yaml` file in Home Assistant
to configure Predbat - see [Configuring apps.yaml](apps-yaml.md#Basics).

When Predbat starts up initially it will perform a sanity check of the AppDaemon configuration itself and confirm the right files are present.
You will see this check in the log, should it fail a warning will be issued and **predbat.status** will also reflect the warning.
While the above warning might not prevent Predbat from starting up, you should fix the issue ASAP as it may cause future problems.

## Predbat Output and Configuration Controls

As described above, the basic configuration of Predbat is held in the `apps.yaml` configuration file.

When Predbat first runs it will create a number of output and configuration control entities in Home Assistant which are used to fine-tune how Predbat operates.
The entities are all prefixed *predbat* and can be seen (and changed) from the Settings / Devices & Services / Entities list in Home Assistant.

It is recommended that you create a dashboard page with all the required entities to control Predbat
and another page to display Predbat's charging and discharging plan for your battery.

The [Output Data](output-data.md) section describes these points in more detail.

The Home Assistant entity **predbat.status** contains details of what status Predbat is currently in (e.g. Idle, Charging, Error).
Detailed progress messages and error logging is written to the Predbat logfile which you can view within Home Assistant using a file editor.

The [Predbat Configuration Guide](configuration-guide.md) gives an overview of the main Predbat configuration items and
detail of 'standard Predbat configuration' settings for different electricity tariff types - e.g. a cheap overnight rate,
multiple import rates during the day, and variable tariffs such as Agile, etc.

The detailed [Predbat Customisation Guide](customisation.md) details all the Predbat configuration items (switches, input numbers, etc) in Home Assistant, and what each of them does.

## Ready to light the touch-paper

By now you should have successfully installed and configured Predbat in AppDaemon and the other components it is dependent upon (e.g. GivTCP, Solcast, Octopus Integration).

![image](https://github.com/springfall2008/batpred/assets/48591903/48cffa4a-5f05-4cbc-9356-68eb3d8fb730)

You have checked the [Predbat AppDaemon log file](output-data.md#predbat-logfile) doesn't have any errors (there is a lot of output in the logfile, this is normal).

You have configured predbat's control entities, created a couple of dashboard pages to control and monitor Predbat, and are ready to start Predbat running.

In order to enable Predbat you must delete the 'template: True' line in `apps.yaml` once you are happy with your configuration.

You may initially want to set **select.predbat_mode** to *Monitor* to see how Predbat operates, e.g. by studying the [Predbat Plan](predbat-plan-card.md).
In *Monitor* mode Predbat will monitor (but not change) the current inverter settings and predict the battery SoC based on predicted Solar Generation and House Load.<BR>
NB: In *Monitor* mode Predbat will *NOT* plan any battery charge or discharge activity of its own,
it will report on the predicted battery charge level based on the current inverter charge & discharge settings, predicted house load and predicted solar generation.

The recommended next step is to start Predbat planning your inverter charging and discharging activity but not (yet) make any changes to the inverter.
This enables you to get a feel for the Predbat plan and [customise Predbat's settings](customisation.md) to meet your needs.

Set **select.predbat_mode** to the correct [mode of operation](customisation.md#predbat-mode) for your system - usually 'Control charge' or 'Control charge & discharge'.
ALSO you should set **switch.predbat_set_read_only** to True to stop Predbat making any changes to your inverter.

Once you are happy with the plan Predbat is producing, and are ready to let Predbat start controlling your inverter charging and discharging,
set the switch **switch.predbat_set_read_only** to False and Predbat will start controlling your inverter.

You can see the planned charging and discharging activity in the [Predbat Plan](predbat-plan-card.md).

## Updating Predbat

Note that any future updates to Predbat will not overwrite the `apps.yaml` configuration file that you have tailored to your setup.
If new Predbat releases introduce new features to apps.yaml you may therefore need to manually copy across the new apps.yaml settings from the [Template apps.yaml](apps-yaml.md#templates).

## Update via Home Assistant

**Recommended**

Predbat can now be updated using the Home Assistant update feature. We a new release is available you should see it in settings:

![image](https://github.com/springfall2008/batpred/assets/48591903/516c77b8-7258-45e7-868f-eea40ee380ac)

Click on the update and select install:

![image](https://github.com/springfall2008/batpred/assets/48591903/e708899d-a4aa-4bd4-b7d1-1c6687dd7e23)

## HACS Update

**Not Recommended**

HACS checks for updates and new releases only once a day by default, you can however force it to check again, or download a specific version
by using the 'Redownload' option from the top-right three dots menu for Predbat in the HACS Automation section.

**NOTE:** If you update Predbat through HACS you may need to restart AppDaemon as it sometimes reads the config wrongly during the update.
(If this happens you will get a template configuration error in the entity **predbat.status**).<BR>
Go to Settings, Add-ons, AppDaemon, and click 'Restart'.

If you update Predbat via Home Assistant or via its build-in update then HACS will not know about this.

## Predbat built-in update

**Recommended for manual selection of versions or automatic updates**

Predbat can now update itself, just select the version of Predbat you want to install from the **select.predbat_update** drop down menu,
the latest version will be at the top of the list. Predbat will update itself and automatically restart.

Alternatively, if you turn on **switch.predbat_auto_update**, Predbat will automatically update itself as new releases are published on Github.

![image](https://github.com/springfall2008/batpred/assets/48591903/56bca491-1069-4abb-be29-a50b0a67a6b9)

If you have used the [Combined AppDaemon and Predbat add-on installation method](#appdaemon-predbat-combined-install) then
once installed and configured you should update Predbat to the latest version by using the **select.predbat_update** selector or by enabling the **switch.predbat_auto_update**.

## Manual update of Predbat

**Expert only**

You can go to Github and download predbat.py from the releases tab and then manually copy this file
over the existing version in `/config/appdaemon/apps/batpred/` manually.
