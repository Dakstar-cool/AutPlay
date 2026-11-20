"""Independent predecessor definitions for the integrated production 0109 release."""

from types import MappingProxyType
from typing import Final

COMPATIBLE_HEAD: Final = "0109_sona_prod_privacy"
PURGE_READY_SHA256: Final = "e83ef2eee46072e9242cf65d51674b46321502e59d7bb6f0b1e8512f3cf6f7f0"
COMPATIBLE_ROUTE_PINS: Final = MappingProxyType({
    "account.purge_account(uuid,uuid)": (
        "991245c0724df60a97d24639efa9572f3abe50233298ea7b870561afd688ecfe"
    ),
    "app_private.assert_native_offline_pack_v2(uuid)": (
        "c8e926b35b29a44d046b8dd27fea0813c7b3af90b981ad36b200fda85dfd1062"
    ),
    "app_private.assert_native_served_authority(uuid)": (
        "2f3ee9482f8ea97a4efb77bd55598e2197bea35d41acc022c3f307571d393f91"
    ),
    "app_private.commit_native_atomic_capture(uuid,uuid,uuid,uuid,uuid,bigint,jsonb)": (
        "ea77a90926e175b8d8e3ae952e18590117f99b80d950c48ae13e4fd35eadde9e"
    ),
    "app_private.commit_native_atomic_served(uuid,uuid,uuid,uuid,bigint,jsonb,uuid)": (
        "5b4b0e196bb60385d66a86e6602638f5352b7b6b424225ba0857e148303ae4c2"
    ),
    "app_private.commit_native_served_authority(uuid,uuid,uuid,uuid,bigint,jsonb)": (
        "8f2714fc911f583425b594e67cfa06ad0b5273e2ccbad0ed7de1bfe6309449a9"
    ),
    "app_private.create_native_offline_pack_v2(uuid,uuid,uuid,uuid,bigint,uuid,uuid)": (
        "ac807e0e3c8b2ae1f35731755c35059a38c17805d80fc619127be300f7cdea1b"
    ),
    "app_private.ensure_native_served_expiry(uuid,uuid)": (
        "12731e9ff2b4050fabbcbd790adc86289154771f9fa55004594302c59bd58c91"
    ),
    "app_private.expire_native_served_authority(uuid,uuid)": (
        "cc9e30fdb4d4f4c90055b76a37a08c5f82c655878f90bd1626c8c5b5be71abd9"
    ),
    "app_private.guard_native_request_write_v3()": (
        "3062873704cafb488a5b27cc0128c5665ac23177960a83509b392c4232da86e9"
    ),
    "app_private.native_pack_payload_v2(ml.offline_pack_binding_receipt)": (
        "e7d507d664a43b439c89b499cd5a07e304492dc01ce794ef505c2f4e58461cd1"
    ),
    "app_private.native_served_decision_digest(uuid)": (
        "d7aece3bb9a45d6333bdeb3927ad4da24a9c5db2c7255fd2f8115e2fa36c099b"
    ),
    "app_private.protect_native_served_authority()": (
        "1ef80fbd256f01a2300f6d40dfa0c6304b0266f24e757e5754cde01bc3205c4b"
    ),
    "app_private.purge_native_served_authority(uuid,uuid)": (
        "e2b49bf7ca74f98dba3a6330872eeb5777baa09fb30cc48816633028c6316557"
    ),
    "app_private.purge_native_served_expiry_receipts(uuid,uuid)": (
        "0a39303eaebe6f7a130a59e373c31922353559df387b8b9bcbea158789475f4b"
    ),
    "app_private.purge_native_served_owner(uuid)": (
        "cf944e17dafb46b70e95d7d3ed552602a3320e0f58daa1a7a093f4b5b4bd9373"
    ),
    "app_private.read_native_offline_pack_v2(uuid,uuid,uuid,uuid,bigint,uuid,boolean)": (
        "93289359f62f076f5b8781a5be0b45da723b95b02dc0142fa72eca5c37940c57"
    ),
    "app_private.read_native_served_authority(uuid,uuid)": (
        "f6197f79a622ab5dc8430951267ac08501af029c8aabf79e1164b977f872ec31"
    ),
})
