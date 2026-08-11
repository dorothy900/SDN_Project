#!/bin/bash
# Start Mininet Topology - interactive CLI, no controller
#
# Uses no controller and failMode=secure, matching the project's actual,
# verified deployment approach (see mininet_path_verification.py and
# mininet_failure_recovery_demo.py in this directory): rules are pushed
# directly via ovs-ofctl, not mediated by a controller like OpenDaylight.
# failMode=secure (not the Mininet default "standalone") matters here
# because GEANT is cyclic -- standalone's implicit L2-learning fallback
# floods unmatched traffic forever on a cycle (confirmed: a broadcast storm
# during earlier testing). secure drops unmatched traffic by default instead.
#
# For automated, repeatable verification (not just manual poking around),
# use mininet_path_verification.py or mininet_failure_recovery_demo.py
# instead -- both build this same topology and additionally push real
# rules, check ping results, and save a report under results/.

echo "Starting Mininet Geant Topology (no controller, failMode=secure)..."
sudo -E python3 -c "
from topology import GeantTopology
from mininet.net import Mininet
from mininet.node import OVSSwitch
from mininet.cli import CLI

topo = GeantTopology()
net = Mininet(topo=topo, switch=lambda name, **kw: OVSSwitch(name, failMode='secure', **kw), controller=None)
try:
    net.start()
    CLI(net)
finally:
    net.stop()
"
