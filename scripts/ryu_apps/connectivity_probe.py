#!/usr/bin/env python3
"""
Connectivity Probe - minimal RYU app confirming real OVS switches actually
connect to this controller over OpenFlow and that periodic port-stats
requests get real replies back, before investing in the full stability-aware
TE app (NetworkState/GraphBuilder/DecisionEngine as RYU callbacks).

Run as (from ryu-env): ryu-manager scripts/ryu_apps/connectivity_probe.py
"""
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.ofproto import ofproto_v1_3

POLL_INTERVAL_S = 5


class ConnectivityProbe(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.datapaths = {}
        hub.spawn(self._poll_loop)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        self.logger.info("*** SWITCH CONNECTED dpid=%s", datapath.id)
        self.datapaths[datapath.id] = datapath

        # Table-miss rule (send unmatched packets to the controller) -- not required
        # for the stats-only probe, but confirms OFPFlowMod round-trips too, since
        # the full app will need to push real flow rules the same way.
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=0, match=parser.OFPMatch(), instructions=inst)
        datapath.send_msg(mod)

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
        for stat in ev.msg.body:
            self.logger.info(
                "*** REAL STATS dpid=%s port=%s rx_bytes=%s tx_bytes=%s rx_dropped=%s tx_dropped=%s",
                dpid, stat.port_no, stat.rx_bytes, stat.tx_bytes, stat.rx_dropped, stat.tx_dropped,
            )
