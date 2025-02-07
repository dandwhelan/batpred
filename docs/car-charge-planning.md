# Car charging planning

There are two ways to plan car charging slots:

- If you have Intelligent Octopus import tariff and the Octopus Energy integration - in which case Predbat will use the slots allocated by Octopus Energy in battery prediction.
The [Octopus Energy integration supports Octopus Intelligent](https://bottlecapdave.github.io/HomeAssistant-OctopusEnergy/entities/intelligent/),
and through that Predbat gets most of the information it needs.
    - **octopus_intelligent_slot** in `apps.yaml` is pre-configured with a regular expression to point to the Intelligent Slot sensor in the Octopus Energy integration.
You should not need to change this, but its worth checking the [Predbat logfile](output-data.md#predbat-logfile) to confirm that it has found your Octopus account details
    - Set **switch.predbat_octopus_intelligent_charging** to True
    - Information about the car's battery size will be automatically extracted from the Octopus Energy integration
    - You should set the cars current soc sensor, **car_charging_soc** in `apps.yaml` to point to a sensor that specifies the car's current % charge level to have accurate results.
This should normally be a sensor provided by your car charger. If you don't have this available for your charger then Predbat will assume the charge level is 0%.
    - If you set **car_charging_limit** in `apps.yaml` then Predbat can also know if the car's limit is set lower than in Intelligent Octopus.
    If you don't set this Predbat will default to 100%.
    - You can use **car_charging_now** as a workaround to indicate your car is charging but the Intelligent API hasn't reported it.
    - Let the Octopus app control when your car charges

- Predbat-led charging - Here Predbat plans the charging based on the upcoming low rate slots
    - Ensure **car_charging_limit**, **car_charging_soc** and **car_charging_planned** are set correctly in `apps.yaml`
    - Set **select.predbat_car_charging_plan_time** in Home Assistant to the time you want the car ready by
    - Enable **switch.predbat_car_charging_plan_smart** if you want to use the cheapest slots only.
    If you leave this disabled then all low rate slots will be used. This may mean you need to use expert mode and change your low rate
    threshold to configure which slots should be considered if you have a tariff with more than 2 import rates (e.g. flux)
    - Use an automation based on **binary_sensor.predbat_car_charging_slot** to control when your car charges
    - _WARNING: Do not set **car_charging_now** or you will create a circular dependency._

NOTE: Multiple cars can be planned with Predbat.

See [Car charging filtering](apps-yaml.md#car-charging-filtering) and [Planned car charging](apps-yaml.md#planned-car-charging)
in the [apps.yaml settings](apps-yaml.md) section of the documentation.
