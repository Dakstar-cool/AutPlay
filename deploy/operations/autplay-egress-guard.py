#!/usr/bin/env python3
"""Render/apply an independent nftables boundary for AutPlay Docker bridges."""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import subprocess

TABLE = "autplay_egress"
PRIVATE_V4 = (
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "224.0.0.0/4",
    "240.0.0.0/4",
)
PRIVATE_V6 = ("::/128", "::1/128", "fc00::/7", "fe80::/10", "fec0::/10", "ff00::/8")


def topology() -> tuple[list[str], list[str], list[str]]:
    output = subprocess.check_output(["docker", "network", "ls", "--format", "{{json .}}"])
    networks = [json.loads(line) for line in output.decode().splitlines()]
    names = [n["Name"] for n in networks if n["Name"].startswith("autplay-")]
    if not names:
        raise RuntimeError("No AutPlay networks found")
    records = json.loads(subprocess.check_output(["docker", "network", "inspect", *names]))
    bridges, ipv4, ipv6 = [], [], []
    for network in records:
        if network["Driver"] != "bridge":
            raise RuntimeError("AutPlay egress guard requires bridge networks")
        bridge = network.get("Options", {}).get(
            "com.docker.network.bridge.name", "br-" + network["Id"][:12]
        )
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", bridge):
            raise RuntimeError("Invalid bridge interface")
        bridges.append(bridge)
        for config in network["IPAM"]["Config"]:
            subnet = ipaddress.ip_network(config["Subnet"])
            (ipv4 if subnet.version == 4 else ipv6).append(str(subnet))
    return sorted(set(bridges)), sorted(set(ipv4)), sorted(set(ipv6))


def render(bridges: list[str], ipv4: list[str], ipv6: list[str], *, replace: bool) -> str:
    if not bridges:
        raise ValueError("At least one bridge is required")
    interfaces = ", ".join(json.dumps(name) for name in bridges)
    lines = [f"delete table inet {TABLE}"] if replace else []
    lines.extend(
        (
            f"table inet {TABLE} {{",
            "  chain host_input {",
            "    type filter hook input priority -5; policy accept;",
            f"    iifname {{ {interfaces} }} ct state established,related accept",
            f"    iifname {{ {interfaces} }} tcp dport {{ 53, 443 }} accept",
            f"    iifname {{ {interfaces} }} udp dport 53 accept",
            f"    iifname {{ {interfaces} }} counter reject with icmpx type admin-prohibited",
            "  }",
            "  chain container_forward {",
            "    type filter hook forward priority -5; policy accept;",
            f"    iifname {{ {interfaces} }} ct state established,related accept",
        )
    )
    for family, subnets in (("ip", ipv4), ("ip6", ipv6)):
        if subnets:
            lines.append(
                f"    iifname {{ {interfaces} }} {family} daddr {{ {', '.join(subnets)} }} accept"
            )
    for family, subnets in (("ip", PRIVATE_V4), ("ip6", PRIVATE_V6)):
        lines.append(
            f"    iifname {{ {interfaces} }} {family} daddr {{ {', '.join(subnets)} }} "
            "counter reject with icmpx type admin-prohibited"
        )
    lines.extend(("  }", "}"))
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("render", "check", "apply", "remove"))
    arguments = parser.parse_args()
    exists = (
        subprocess.run(["nft", "list", "table", "inet", TABLE], capture_output=True).returncode == 0
    )
    if arguments.action == "remove":
        if exists:
            subprocess.run(["nft", "delete", "table", "inet", TABLE], check=True)
        return
    policy = render(*topology(), replace=exists)
    if arguments.action == "render":
        print(policy, end="")
        return
    subprocess.run(["nft", "--check", "--file", "-"], input=policy.encode(), check=True)
    if arguments.action == "apply":
        subprocess.run(["nft", "--file", "-"], input=policy.encode(), check=True)


if __name__ == "__main__":
    main()
