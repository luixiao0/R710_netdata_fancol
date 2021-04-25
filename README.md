# R710_netdata_fancol
control Dell R710 fan speed with netdata's data via IPMItool (GPU pwoer considered)

requirements:
  python:
    pip install aiohttp python-netdata numpy
    
  IPMItool of your system, a working netdata daemon (my case is a docker container from unraid)
  it can be run at your router(like openwrt) or another server
