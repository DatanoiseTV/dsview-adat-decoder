# Installing the decoder

DSView loads protocol decoders from a `decoders` directory, one Python package
per decoder. Installing means copying the `adat` directory from this
repository into it. The `adat` package is `__init__.py` and `pd.py`; nothing
else is needed at run time.

The decoders shipped in DSView are identical to the upstream ones in
[`libsigrokdecode4DSL/decoders`](https://github.com/DreamSourceLab/DSView/tree/master/libsigrokdecode4DSL/decoders),
and this decoder is written for the same API (version 3).

## macOS

The decoder directory is inside the application bundle:

    /Applications/DSView.app/Contents/MacOS/decoders

The app is code signed. Adding files to the bundle invalidates the signature,
and macOS may then refuse to launch it. Options:

- Install from a DSView build you control, where re-signing after the copy
  is yours to do.
- Keep a separate, modified copy of the app and leave the original untouched.

Check that the app still starts after the change before relying on it.

## Linux

Copy into the data directory of your installation:

    <prefix>/share/DSView/decoders

For a package install this is usually `/usr/share/DSView/decoders`, for a
build from source `/usr/local/share/DSView/decoders`.

## After copying

Restart DSView. The decoder appears as "ADAT Lightpipe" in the decoder list
(short name `ADAT`). Add it and assign the receiver output to the `Data`
channel. See [usage](usage.md) for settings.

## Python version

DSView embeds Python 3.7 and there is no standalone interpreter to upgrade.
The decoder uses nothing newer; CI parses `adat/` with
`ast.parse(..., feature_version=(3, 7))`. If you modify the code, keep to that: no walrus operator, no
`f"{x=}"`, no positional-only parameters.
