# PASTEL conformance suite

Tests every device must pass, on every platform it supports. They use only
the device descriptor, so they run unchanged against any device pack:

    pytest --pyargs soc_pastel.conformance --device=veerwolf --platform=verilator
    pytest --pyargs soc_pastel.conformance --device=veerwolf --platform=verilator --code-location=ccm
