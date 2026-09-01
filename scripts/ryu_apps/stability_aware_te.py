#!/usr/bin/env python3
"""
Stability-Aware TE - RYU app embedding this project's real decision stack
(NetworkState/GraphBuilder/DecisionEngine, unmodified) directly inside the
controller process, the same pattern Kamarudin and Ameedeen (2025) use for
their own routing app.

Steps 1-3 (switch registration, LLDP topology discovery, real telemetry ->
NetworkState via StatisticsCollector's reusable rate/utilization/loss math)
were confirmed against the real 40-switch/61-edge GEANT topology: all 40
switches connected, all 122 discovered link directions (61 edges x 2)
matched, zero errors.

Steps 4-5 (this file, full): real path installation via OFPFlowMod, and a
periodic decision loop -- reusing experiments/simulation_common.py's
ProposedDriver *completely unmodified*, just fed real telemetry instead of
synthetic set_link_condition() calls, with its path changes shadowed into
real OFPFlowMod pushes instead of its own offline FlowInstaller's no-op
text-only output.

Link up/down: EventLinkAdd/EventLinkDelete (LLDP) and EventOFPPortStatus
(switch-reported) all feed NetworkState.set_link_status(), so the resilience
layer's flap signal + correlated-flap discount actually fire in the live
deployment, not only under the offline harness. The decision loop runs on
wall-clock time() so those async events and the driver's decay timers share
one clock.

HOST_PORT=1 (every switch's host-facing port): not guessed -- topology.py's
build() always adds the host link first for each switch, and Mininet
assigns ports in link-creation order; empirically confirmed against the
real 40-switch run (grep across all 122 discovered inter-switch link
directions: port=1 never once appears as an inter-switch port).

Needs RYU's built-in LLDP topology discovery to learn which port on each
switch faces which neighbor switch -- there's no shell access to switches
from the controller side, so this is the real equivalent of the Mininet
scripts' own get_ofport() (which queries ovs-vsctl directly on the switch).
ARP is not handled here -- matching this project's existing Mininet scripts'
own convention (static `arp -s` entries set on the hosts directly), not
something this app needs to solve. Run with --observe-links:

    ryu-manager --observe-links scripts/ryu_apps/stability_aware_te.py
"""
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.ofproto import ofproto_v1_3
from ryu.topology import event as topo_event

from experiments.simulation_common import ProposedDriver, link_id as _pair_link_id
from src.monitor.link_capacity import build_node_mapping, resolve_link_capacity_mbps
from src.monitor.models import LinkStatistics, PortStatistics
from src.monitor.network_state import NetworkState
from src.monitor.statistics_collector import StatisticsCollector

POLL_INTERVAL_S = 5
DECISION_INTERVAL_S = 5
HOST_PORT = 1

# (src, dst, utilization_threshold_override) -- override is None for
# config/decision.yaml's real production default (0.7). The one entry below uses a
# lower demonstration threshold specifically because this VM's own iperf UDP
# generation ceiling (~50-55Mbps, established in this project's other real-hardware
# scripts -- see e.g. scripts/mininet_independence_check.py's own docstring) can't
# reach 70% of link 0-4's 100Mbps configured capacity, but can comfortably reach
# 30%: this demonstrates the real reroute mechanism firing under genuinely real,
# induced congestion, without needing this one VM to physically saturate a 70Mbps
# link. Production deployments read the real 0.7 threshold from config/decision.yaml
# via override=None; this is a real-hardware-testbed calibration choice, not a
# change to the production default itself.
MONITORED_PAIRS = [("0", "12", 0.3)]


def _link_id(u: str, v: str) -> str:
    """Same sorted-pair convention as experiments/simulation_common.py's link_id()."""
    nodes = sorted([str(u), str(v)])
    return "%s-%s" % (nodes[0], nodes[1])


class StabilityAwareTE(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # graph_node_id -> (switch_name, host_name), the same single source of truth
        # topology.py's real Mininet build() uses -- see build_node_mapping's docstring.
        self.node_mapping = build_node_mapping()
        self.switch_to_node = {sw: node for node, (sw, _) in self.node_mapping.items()}

        self.datapaths = {}  # dpid -> datapath
        # (dpid, port_no) -> neighbor dpid, populated from LLDP-discovered links.
        self.port_to_neighbor_dpid = {}
        # (dpid, neighbor_dpid) -> port_no -- the reverse index _install_path needs to
        # find the real egress port toward a specific next hop.
        self.dpid_pair_to_port = {}
        self._seeded_capacity_ports = set()  # (switch_name, port_no) already given a real capacity

        self.collector = StatisticsCollector(output_dir=PROJECT_ROOT / "results" / "ryu_live")
        self.state = NetworkState()

        # One real, unmodified ProposedDriver per monitored pair -- the exact same class
        # experiments/simulation_common.py's offline harness uses, just driven by this
        # app's real telemetry instead of synthetic set_link_condition() calls.
        self.drivers = {}
        self._installed_paths = {}  # (src, dst) -> the path currently pushed as real FlowMod rules

        hub.spawn(self._poll_loop)
        hub.spawn(self._decision_loop)

    def _node_id_for_dpid(self, dpid: int):
        return self.switch_to_node.get("s%d" % dpid)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        self.datapaths[datapath.id] = datapath
        node_id = self._node_id_for_dpid(datapath.id)
        self.logger.info("*** SWITCH CONNECTED dpid=%s -> GEANT node %s", datapath.id, node_id)

        # A (re)connecting switch has an empty flow table -- any path this app previously
        # believed was "installed" through this node no longer actually has rules on the
        # switch. Without this, a switch restart (or this app itself reconnecting to a
        # fresh Mininet session) would leave _evaluate_pair() thinking a path is still
        # live when it silently isn't, since driver.path itself wouldn't have changed.
        if node_id is not None:
            stale_pairs = [
                pair for pair, path in self._installed_paths.items()
                if node_id in path
            ]
            for pair in stale_pairs:
                del self._installed_paths[pair]
                self.logger.info("*** Invalidated cached path for %s (dpid=%s reconnected)", pair, datapath.id)

        # Table-miss rule (priority 0) -- real per-path rules from _install_path() are
        # pushed at priority 100, above this; unmatched traffic still needs somewhere
        # to go (the controller, harmlessly, since this app does no L2 learning).
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=0, match=parser.OFPMatch(), instructions=inst)
        datapath.send_msg(mod)

    @set_ev_cls(topo_event.EventLinkAdd)
    def _link_add_handler(self, ev):
        link = ev.link
        self.port_to_neighbor_dpid[(link.src.dpid, link.src.port_no)] = link.dst.dpid
        self.port_to_neighbor_dpid[(link.dst.dpid, link.dst.port_no)] = link.src.dpid
        self.dpid_pair_to_port[(link.src.dpid, link.dst.dpid)] = link.src.port_no
        self.dpid_pair_to_port[(link.dst.dpid, link.src.dpid)] = link.dst.port_no
        src_node = self._node_id_for_dpid(link.src.dpid)
        dst_node = self._node_id_for_dpid(link.dst.dpid)
        if src_node is not None and dst_node is not None:
            self.logger.info(
                "*** LINK DISCOVERED dpid=%s:port=%s <-> dpid=%s:port=%s (GEANT %s)",
                link.src.dpid, link.src.port_no, link.dst.dpid, link.dst.port_no,
                _link_id(src_node, dst_node),
            )
            # Feed the transition into NetworkState. The link's first-ever
            # EventLinkAdd is a no-op flap-wise (set_link_status skips the
            # first observation); a later one -- LLDP re-discovering the link
            # after an EventLinkDelete -- registers a real "up" transition,
            # which is what LinkFlapTracker and the correlated-flap discount
            # need to see. Wall-clock, matching _decision_loop's own clock.
            self.state.set_link_status(_link_id(src_node, dst_node), is_up=True)

    @set_ev_cls(topo_event.EventLinkDelete)
    def _link_delete_handler(self, ev):
        """
        LLDP stopped seeing this link -- feed a real "down" transition into
        NetworkState so it drops out of get_active_graph() and the flap tracker
        counts it. Paired with EventLinkAdd's "up" above; the two are what make
        the resilience layer's flap signal actually fire in the live deployment
        (it was previously only exercised by the offline set_link_condition
        harness). EventOFPPortStatus below catches the faster local case.
        """
        link = ev.link
        src_node = self._node_id_for_dpid(link.src.dpid)
        dst_node = self._node_id_for_dpid(link.dst.dpid)
        if src_node is None or dst_node is None:
            return
        lid = _link_id(src_node, dst_node)
        self.logger.info("*** LINK LOST (GEANT %s) -- marking down", lid)
        self.state.set_link_status(lid, is_up=False)

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def _port_status_handler(self, ev):
        """
        A switch telling the controller a port went down/up directly -- faster
        and more reliable than waiting for LLDP to time out. OFPPR_DELETE, or
        OFPPR_MODIFY with OFPPS_LINK_DOWN set, is a real link-down; the reverse
        is a link-up. Redundant with the LLDP handlers by design: whichever
        arrives first records the transition, the second is a no-op
        (set_link_status skips a same-status report).
        """
        msg = ev.msg
        ofproto = msg.datapath.ofproto
        dpid = msg.datapath.id
        port_no = msg.desc.port_no
        neighbor_dpid = self.port_to_neighbor_dpid.get((dpid, port_no))
        src_node = self._node_id_for_dpid(dpid)
        dst_node = self._node_id_for_dpid(neighbor_dpid) if neighbor_dpid is not None else None
        if src_node is None or dst_node is None:
            return  # host-facing or not-yet-discovered port
        lid = _link_id(src_node, dst_node)

        if msg.reason == ofproto.OFPPR_DELETE:
            is_up = False
        elif msg.reason == ofproto.OFPPR_ADD:
            is_up = True
        else:  # OFPPR_MODIFY -- read the link-down bit
            is_up = not bool(msg.desc.state & ofproto.OFPPS_LINK_DOWN)
        self.logger.info("*** PORT STATUS %s port=%s (GEANT %s) -> %s",
                         dpid, port_no, lid, "up" if is_up else "down")
        self.state.set_link_status(lid, is_up=is_up)

    def _poll_loop(self):
        while True:
            for datapath in list(self.datapaths.values()):
                parser = datapath.ofproto_parser
                req = parser.OFPPortStatsRequest(datapath, 0, datapath.ofproto.OFPP_ANY)
                datapath.send_msg(req)
            hub.sleep(POLL_INTERVAL_S)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        switch_name = "s%d" % dpid
        src_node = self._node_id_for_dpid(dpid)
        if src_node is None:
            return  # a switch outside this project's known GEANT mapping

        ofproto = ev.msg.datapath.ofproto
        now = datetime.now()
        raw_stats = []
        for stat in ev.msg.body:
            port_no = stat.port_no
            if port_no > ofproto.OFPP_MAX:
                continue  # reserved port (LOCAL, CONTROLLER, ...), not a real inter-switch link
            neighbor_dpid = self.port_to_neighbor_dpid.get((dpid, port_no))
            if neighbor_dpid is None:
                continue  # host-facing port, or this port's neighbor link not discovered yet
            dst_node = self._node_id_for_dpid(neighbor_dpid)
            if dst_node is None:
                continue

            link = _link_id(src_node, dst_node)
            capacity_key = (switch_name, port_no)
            if capacity_key not in self._seeded_capacity_ports:
                self.collector.set_link_capacity(switch_name, port_no, resolve_link_capacity_mbps(link))
                self._seeded_capacity_ports.add(capacity_key)

            raw_stats.append(PortStatistics(
                timestamp=now, switch=switch_name, port=port_no,
                rx_packets=stat.rx_packets, rx_bytes=stat.rx_bytes,
                tx_packets=stat.tx_packets, tx_bytes=stat.tx_bytes,
                rx_dropped=stat.rx_dropped, tx_dropped=stat.tx_dropped,
            ))

        if not raw_stats:
            return

        rated = self.collector.calculate_rates(raw_stats, sample_time=now.timestamp())
        for port_entry in rated:
            neighbor_dpid = self.port_to_neighbor_dpid[(dpid, port_entry.port)]
            dst_node = self._node_id_for_dpid(neighbor_dpid)
            link = _link_id(src_node, dst_node)
            utilization = self.collector.calculate_utilization(port_entry)
            loss = self.collector.calculate_loss_rate(port_entry)
            # Carry the link's *known* status (set by the port-status / LLDP
            # handlers), not a hard-coded "up" -- otherwise a stray stats reply
            # for a link those handlers just marked down would register a
            # spurious "up" flap through update_link_statistics' own
            # transition check.
            known_status = self.state.link_monitor.get_link_status(link)
            self.state.update_link_statistics(LinkStatistics(
                timestamp=now, link_id=link, utilization=utilization,
                rx_mbps=port_entry.rx_mbps, tx_mbps=port_entry.tx_mbps,
                status=known_status if known_status is not None else "up",
                packet_loss=loss,
            ))
            self.logger.info(
                "*** REAL LINK STATS %s: u=%.4f loss=%.5f rx=%.3fMbps tx=%.3fMbps",
                link, utilization, loss, port_entry.rx_mbps, port_entry.tx_mbps,
            )

    def _dpid_of(self, node_id: str) -> int:
        switch_name, _ = self.node_mapping[node_id]
        return int(switch_name[1:])

    def _egress_port_toward(self, from_node: str, to_node: str) -> Optional[int]:
        """Real egress port at from_node's switch toward to_node's switch (adjacent hop)."""
        return self.dpid_pair_to_port.get((self._dpid_of(from_node), self._dpid_of(to_node)))

    def _install_path(self, path: List[str], src_ip: str, dst_ip: str) -> bool:
        """
        Push real bidirectional flow rules for `path` at every hop -- forward (toward
        dst_ip) and reverse (toward src_ip) -- same match/action shape the offline
        Mininet scripts' own manual ovs-ofctl commands already use (dl_type=0x0800,
        nw_dst=..., actions=output:PORT), just issued as real OFPFlowMod from the
        controller instead of a shell command. Returns False (and pushes nothing) if
        any hop's switch isn't connected yet or a required port hasn't been LLDP-
        discovered yet -- a real, current-state check, not an assumption.
        """
        for index, node in enumerate(path):
            dpid = self._dpid_of(node)
            datapath = self.datapaths.get(dpid)
            if datapath is None:
                self.logger.warning("*** Cannot install path: dpid=%s not connected", dpid)
                return False

            forward_port = HOST_PORT if index == len(path) - 1 else self._egress_port_toward(node, path[index + 1])
            reverse_port = HOST_PORT if index == 0 else self._egress_port_toward(node, path[index - 1])
            if forward_port is None or reverse_port is None:
                self.logger.warning("*** Cannot install path: port not yet discovered at hop %s (dpid=%s)", index, dpid)
                return False

            parser = datapath.ofproto_parser
            ofproto = datapath.ofproto
            for dst, egress in ((dst_ip, forward_port), (src_ip, reverse_port)):
                match = parser.OFPMatch(eth_type=0x0800, ipv4_dst=dst)
                actions = [parser.OFPActionOutput(egress)]
                inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
                mod = parser.OFPFlowMod(datapath=datapath, priority=100, match=match, instructions=inst)
                datapath.send_msg(mod)

        self.logger.info("*** INSTALLED REAL PATH %s (%s -> %s)", "->".join(path), src_ip, dst_ip)
        return True

    def _decision_loop(self):
        hub.sleep(10)  # let switch registration + LLDP discovery settle before the first decision
        while True:
            # Wall-clock, so the driver's timing gates (hold-down, recovery
            # window, churn/jitter/flap decay) and the async link-event
            # handlers -- which call set_link_status() on wall-clock time --
            # all evaluate against one consistent clock. A synthetic counter
            # here would decay the flap penalty against a different clock than
            # the transitions were recorded on (the exact hazard the offline
            # simulation_common.set_link_condition had to be fixed for).
            now_s = time.time()
            for src, dst, threshold_override in MONITORED_PAIRS:
                self._evaluate_pair(src, dst, now_s, threshold_override)
            hub.sleep(DECISION_INTERVAL_S)

    def _host_ip(self, node_id: str) -> str:
        _, host_name = self.node_mapping[node_id]
        return "10.0.0.%s" % host_name[1:]

    def _evaluate_pair(self, src: str, dst: str, now_s: float, threshold_override) -> None:
        """
        Drives one real, unmodified ProposedDriver per monitored pair -- the exact
        DecisionEngine/GraphBuilder/StabilityManager stack the offline harness uses,
        fed this app's own real NetworkState instead of synthetic set_link_condition()
        data. A path change the driver decides on is shadowed into a real OFPFlowMod
        push here; the driver's own internal (offline, no-op) FlowInstaller call still
        runs too, harmlessly, since nothing reads its output in this real deployment.
        """
        driver = self.drivers.get((src, dst))
        if driver is None:
            driver = ProposedDriver(
                self.state, src, dst, config_path=str(PROJECT_ROOT / "config" / "decision.yaml"),
                utilization_threshold=threshold_override,
            )
            self.drivers[(src, dst)] = driver

        hotspot_link = _pair_link_id(driver.path[0], driver.path[1])
        stats = self.state.get_link_stats(hotspot_link)
        hotspot_utilization = stats.utilization if stats is not None else 0.0

        path_before = list(driver.path)
        driver.step(now_s=now_s, hotspot_link=hotspot_link, hotspot_utilization=hotspot_utilization)

        if driver.path != path_before and driver.path != self._installed_paths.get((src, dst)):
            self.logger.info(
                "*** REROUTE DECISION %s->%s: %s -> %s", src, dst, "->".join(path_before), "->".join(driver.path),
            )
            installed = self._install_path(driver.path, self._host_ip(src), self._host_ip(dst))
            if installed:
                self._installed_paths[(src, dst)] = list(driver.path)
        elif (src, dst) not in self._installed_paths:
            # First cycle for this pair -- push its initial path even though it "didn't
            # change" (there was nothing installed before).
            installed = self._install_path(driver.path, self._host_ip(src), self._host_ip(dst))
            if installed:
                self._installed_paths[(src, dst)] = list(driver.path)
