Built-in bay maps. A profile is used when no `~/.config/usbinspect/ports.toml`
exists and its `match` string is contained in the machine's DMI
"`sys_vendor product_name`".

Create one with `usbinspect calibrate`, then `usbinspect ports --export > <name>.toml`.
