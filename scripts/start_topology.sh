#!/bin/bash
# Start Mininet Topology
# Stage 1-2: Topology and Traffic Monitoring

echo "Starting Mininet Geant Topology..."
sudo -E python3 -c "
from topology import GeantTopology
from mininet.net import Mininet
from mininet.node import RemoteController
from mininet.cli import CLI

topo = GeantTopology()
net = Mininet(topo=topo, controller=RemoteController('c0', ip='127.0.0.1', port=6653))
try:
    net.start()
    CLI(net)
finally:
    net.stop()
"
