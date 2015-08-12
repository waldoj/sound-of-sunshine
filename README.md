# Sound of Sunshine

Adjusts my thermostat when my solar array is generating lots of power, and is likely to continue, basically using my house as a battery.

## Overview

Combines data from [Enphase Enlighten’s API](https://enphase.com/en-us/products-and-services/enlighten-and-apps) and the [Current Cost Envi](http://www.currentcost.com/product-cc128.html) to generate a CSV log of energy production/consumption, a static HTML page reporting production/consumption status, and report excess production/consumption via [Pushover](https://pushover.net/) alerts. To come: uses this data to adjust the temperature of a Nest thermostat, using Weather Underground’s API to determine when that is worth doing.

## Satisfying API License Requirements

I need to write the following to satisfy [a license agreement](https://developer.enphase.com/docs#Display-Requirements):

[Powered by Enphase Energy](https://enphase.com/).
