"""Acts handsal accepts, copied from its own conformance vectors.

Source: github.com/Daily-Nerd/handsal at tag v0.4.2, commit
09dc029c2d738c4346d05ea7bf2d9f57b338eb61. Each is the `jws` input of a vector
whose expected answer is `valid:true, reason:ok`. Copied unchanged.
"""

ACCEPTED_ACTS = {
    # conformance/vectors/ok.approve.json: check-act answered
    # valid:true, reason:ok; performer_id in the payload is "alice".
    "ok.approve": (
        "eyJhbGciOiJFUzI1NiIsImtpZCI6IkU1dWExcDlPbVFmNXNsS3hnRFNHUVQxZEotVXVKNDli"
        "X1Qyb3VFcXlyckkifQ.eyJhY3Rpb25fcmVmIjpudWxsLCJiaW5kaW5nIjoibG9jYWwiLCJjb"
        "3N0Ijp7InJhaWxfcmVmIjpudWxsLCJ0b2tlbnMiOiIwIiwidXNkX21pY3JvcyI6IjAiLCJ3Y"
        "WxsX21zIjoiMCJ9LCJleHQiOnsiZGV2LmhhbmRzYWwvYWN0Ijp7ImJvZHkiOnt9LCJleHBpc"
        "mVzIjoiMjAyNi0xMS0wOFQxMjo1OTowMFoiLCJoZWFkIjp7ImlkIjoidUVpQVJEVmZfSkR5L"
        "TByT2pTTTFsRUg2WU1TRWREMlJRaXV2aUwtQXNTVkxxNEEiLCJraW5kIjoiY2hhaW4ifSwia"
        "2lkIjoiRTV1YTFwOU9tUWY1c2xLeGdEU0dRVDFkSi1VdUo0OWJfVDJvdUVxeXJySSIsIm9yZ"
        "yI6InVFaUFSRFZmX0pEeS0wck9qU00xbEVINllNU0VkRDJSUWl1dmlMLUFzU1ZMcTRBIiwic"
        "mVjaXBpZW50IjoiYWxpY2UiLCJyZWxheSI6InIuZXhhbXBsZSIsInJlcXVlc3RfaWQiOiJyZ"
        "XEtMDAwMDAwMDEifX0sImlucHV0c19oYXNoIjoidUVpQ3dtQWRvRUJhZ0JkY0poeEpxS2FQR"
        "Dc4d1NBb3lRbVgzZmotXzhHbFN3UlEiLCJsb2dfcG9saWN5IjoiYmVzdF9lZmZvcnQiLCJtZ"
        "XRob2QiOiJsb2NhbDpoYW5kc2FsLnJlcXVlc3QuYXBwcm92ZSIsIm5vbmNlIjoidUVpQ1pIR"
        "zAyX3NiaDJVUzFOYy01emdmSXdvck03NGNMRW96eHFuVjI3MGRueFEiLCJvdXRwdXRzX2hhc"
        "2giOiJ1RWlEYlVHSmhOTlBNSDM3YUJyRFQ5aE01UXBmb3RmalRiX2JKV2Z0RHlYcEY2dyIsI"
        "nBhcmVudF9wZXJmb3JtZXJfaWQiOm51bGwsInBhcmVudF9yZWNlaXB0X2hhc2giOm51bGwsI"
        "nBlcmZvcm1lcl9pZCI6ImFsaWNlIiwicmVhc29uIjpudWxsLCJyZXF1ZXN0ZXJfaWQiOm51b"
        "GwsInN0YXR1cyI6Ik9LIiwidHMiOiIyMDI2LTExLTA4VDExOjU5OjAwWiIsInYiOiJ2aXRua"
        "S8wLjIifQ.LiUFwREYkJ1bJGbX-zRgUa7ZywC0NzPa5NgB8kfgxfo2OTc3YKJGc1zD0UaMh_"
        "8CtYO1RUm-YP26q38Gxh3dTw"
    ),
    # conformance/vectors/ok.reject.json: check-act answered
    # valid:true, reason:ok; performer_id in the payload is "alice".
    "ok.reject": (
        "eyJhbGciOiJFUzI1NiIsImtpZCI6IkU1dWExcDlPbVFmNXNsS3hnRFNHUVQxZEotVXVKNDli"
        "X1Qyb3VFcXlyckkifQ.eyJhY3Rpb25fcmVmIjpudWxsLCJiaW5kaW5nIjoibG9jYWwiLCJjb"
        "3N0Ijp7InJhaWxfcmVmIjpudWxsLCJ0b2tlbnMiOiIwIiwidXNkX21pY3JvcyI6IjAiLCJ3Y"
        "WxsX21zIjoiMCJ9LCJleHQiOnsiZGV2LmhhbmRzYWwvYWN0Ijp7ImJvZHkiOnt9LCJleHBpc"
        "mVzIjoiMjAyNi0xMS0wOFQxMjo1OTowMFoiLCJoZWFkIjp7ImlkIjoidUVpQVJEVmZfSkR5L"
        "TByT2pTTTFsRUg2WU1TRWREMlJRaXV2aUwtQXNTVkxxNEEiLCJraW5kIjoiY2hhaW4ifSwia"
        "2lkIjoiRTV1YTFwOU9tUWY1c2xLeGdEU0dRVDFkSi1VdUo0OWJfVDJvdUVxeXJySSIsIm9yZ"
        "yI6InVFaUFSRFZmX0pEeS0wck9qU00xbEVINllNU0VkRDJSUWl1dmlMLUFzU1ZMcTRBIiwic"
        "mVjaXBpZW50IjoiYWxpY2UiLCJyZWxheSI6InIuZXhhbXBsZSIsInJlcXVlc3RfaWQiOiJyZ"
        "XEtMDAwMDAwMDEifX0sImlucHV0c19oYXNoIjoidUVpQ3dtQWRvRUJhZ0JkY0poeEpxS2FQR"
        "Dc4d1NBb3lRbVgzZmotXzhHbFN3UlEiLCJsb2dfcG9saWN5IjoiYmVzdF9lZmZvcnQiLCJtZ"
        "XRob2QiOiJsb2NhbDpoYW5kc2FsLnJlcXVlc3QucmVqZWN0Iiwibm9uY2UiOiJ1RWlBV1BCe"
        "jlpZ3RBMXFCU2g1bWxnWk5SRE5nMnc4aFRQV0ZrcERvYnVZR1RWQSIsIm91dHB1dHNfaGFza"
        "CI6InVFaUJMNXRoTElHckJXVndKeU55dkM5MDNxbXozYzE0YTdzbXhFdHlJcVZOQ1V3Iiwic"
        "GFyZW50X3BlcmZvcm1lcl9pZCI6bnVsbCwicGFyZW50X3JlY2VpcHRfaGFzaCI6bnVsbCwic"
        "GVyZm9ybWVyX2lkIjoiYWxpY2UiLCJyZWFzb24iOm51bGwsInJlcXVlc3Rlcl9pZCI6bnVsb"
        "Cwic3RhdHVzIjoiT0siLCJ0cyI6IjIwMjYtMTEtMDhUMTE6NTk6MDBaIiwidiI6InZpdG5pL"
        "zAuMiJ9.hWscKFNDcHxhKAGL84zdZexfvkQniKeXS-X3clRxTushqPPM2EIIJlPi0G48nKnl"
        "dTikpZwUUWzmZaawCzYEHg"
    ),
    # conformance/vectors/ok.decision.json: check-act answered
    # valid:true, reason:ok; performer_id in the payload is "alice".
    "ok.decision": (
        "eyJhbGciOiJFUzI1NiIsImtpZCI6IkU1dWExcDlPbVFmNXNsS3hnRFNHUVQxZEotVXVKNDli"
        "X1Qyb3VFcXlyckkifQ.eyJhY3Rpb25fcmVmIjpudWxsLCJiaW5kaW5nIjoibG9jYWwiLCJjb"
        "3N0Ijp7InJhaWxfcmVmIjpudWxsLCJ0b2tlbnMiOiIwIiwidXNkX21pY3JvcyI6IjAiLCJ3Y"
        "WxsX21zIjoiMCJ9LCJleHQiOnsiZGV2LmhhbmRzYWwvYWN0Ijp7ImJvZHkiOnsiYWN0aW9uI"
        "joicmF0aWZ5IHJ1bGluZyIsImNvbnN1bWVyIjoibm90ZXMiLCJzdWJqZWN0IjoicnVsaW5nL"
        "TAwMDEifSwiZXhwaXJlcyI6IjIwMjYtMTEtMDhUMTI6NTk6MDBaIiwiaGVhZCI6eyJpZCI6I"
        "nVFaUFSRFZmX0pEeS0wck9qU00xbEVINllNU0VkRDJSUWl1dmlMLUFzU1ZMcTRBIiwia2luZ"
        "CI6ImNoYWluIn0sImtpZCI6IkU1dWExcDlPbVFmNXNsS3hnRFNHUVQxZEotVXVKNDliX1Qyb"
        "3VFcXlyckkiLCJvcmciOiJ1RWlBUkRWZl9KRHktMHJPalNNMWxFSDZZTVNFZEQyUlFpdXZpT"
        "C1Bc1NWTHE0QSJ9fSwiaW5wdXRzX2hhc2giOiJ1RWlEYjhIYi1FRnhnUjRybmwteHBuaHh1d"
        "zJxRlo3NUIzV3o4elFiZ3czbDNkQSIsImxvZ19wb2xpY3kiOiJiZXN0X2VmZm9ydCIsIm1ld"
        "GhvZCI6ImxvY2FsOmhhbmRzYWwuZGVjaXNpb24iLCJub25jZSI6InVFaUFCTlVOSktzMGdtZ"
        "1dDbmlOel9jUHZ1RlRJbHlrVWk4Q3ZxMWdpWFZpNEJ3Iiwib3V0cHV0c19oYXNoIjoidUVpQ"
        "3Z0N1k3V3Y3MnQyWW45Z0FOcUlPcUlMTGdyMzVzX0xsd0lMcjRwTElLSFEiLCJwYXJlbnRfc"
        "GVyZm9ybWVyX2lkIjpudWxsLCJwYXJlbnRfcmVjZWlwdF9oYXNoIjpudWxsLCJwZXJmb3JtZ"
        "XJfaWQiOiJhbGljZSIsInJlYXNvbiI6bnVsbCwicmVxdWVzdGVyX2lkIjpudWxsLCJzdGF0d"
        "XMiOiJPSyIsInRzIjoiMjAyNi0xMS0wOFQxMTo1OTowMFoiLCJ2Ijoidml0bmkvMC4yIn0.A"
        "irXezLlSoz2_IEQmMvpgKvoSn2_JDzHGlmNcdFIrrBUXJp4fTD8SltIxBInLrh8w7teGPFQ3"
        "ArazVVrUKAwjg"
    ),
}

# Genesis signatures from the lineage of the accepted ok.approve vector. They
# are real handsal acts in the same payload format, by three different people.
# They exist to test the payload READER with distinct people and times, not as
# acts a consumer would recheck. Same tag and commit as above.
GENESIS_SIGNATURES = {
    # conformance/vectors/ok.approve.json, input.lineage[0].sigs[0]:
    # a local:handsal.genesis.sign act by alice, ts 2026-11-08T10:00:00Z.
    "genesis.sign.alice": (
        "eyJhbGciOiJFUzI1NiIsImtpZCI6IkU1dWExcDlPbVFmNXNsS3hnRFNHUVQxZEotVXVKNDli"
        "X1Qyb3VFcXlyckkifQ.eyJhY3Rpb25fcmVmIjpudWxsLCJiaW5kaW5nIjoibG9jYWwiLCJjb"
        "3N0Ijp7InJhaWxfcmVmIjpudWxsLCJ0b2tlbnMiOiIwIiwidXNkX21pY3JvcyI6IjAiLCJ3Y"
        "WxsX21zIjoiMCJ9LCJleHQiOnsiZGV2LmhhbmRzYWwvYWN0Ijp7ImJvZHkiOnt9LCJraWQiO"
        "iJFNXVhMXA5T21RZjVzbEt4Z0RTR1FUMWRKLVV1SjQ5Yl9UMm91RXF5cnJJIiwib3JnIjoid"
        "UVpQVJEVmZfSkR5LTByT2pTTTFsRUg2WU1TRWREMlJRaXV2aUwtQXNTVkxxNEEifX0sImluc"
        "HV0c19oYXNoIjoidUVpQVJEVmZfSkR5LTByT2pTTTFsRUg2WU1TRWREMlJRaXV2aUwtQXNTV"
        "kxxNEEiLCJsb2dfcG9saWN5IjoiYmVzdF9lZmZvcnQiLCJtZXRob2QiOiJsb2NhbDpoYW5kc"
        "2FsLmdlbmVzaXMuc2lnbiIsIm5vbmNlIjoidUVpQ29mVUFwaTV4aldBTHNrNHRYOFBEOWppb"
        "jNCOEI5dkdtZl96bGhfUGN6eXciLCJvdXRwdXRzX2hhc2giOiJ1RWlEanNNUkNtUHdjRkpyN"
        "zlNaVpiN2trSjY1QjVHU2JrMHlrbFprYmVGSzRWUSIsInBhcmVudF9wZXJmb3JtZXJfaWQiO"
        "m51bGwsInBhcmVudF9yZWNlaXB0X2hhc2giOm51bGwsInBlcmZvcm1lcl9pZCI6ImFsaWNlI"
        "iwicmVhc29uIjpudWxsLCJyZXF1ZXN0ZXJfaWQiOm51bGwsInN0YXR1cyI6Ik9LIiwidHMiO"
        "iIyMDI2LTExLTA4VDEwOjAwOjAwWiIsInYiOiJ2aXRuaS8wLjIifQ.DXzb72CLcppjyQxzTR"
        "NbnQ-VJ2FqODViLqDoS0bGYRUcknOz_so8vK6fQlx7slqHhJYfpovcrzYkm_jKHTba0w"
    ),
    # conformance/vectors/ok.approve.json, input.lineage[0].sigs[1]:
    # a local:handsal.genesis.sign act by carol, ts 2026-11-08T10:00:00Z.
    "genesis.sign.carol": (
        "eyJhbGciOiJFUzI1NiIsImtpZCI6Ik5BanRmSzh1UHg5bVRZM2NXdUYxeURJSjREVWhpWER5"
        "UzgtXzdoWU5yYVkifQ.eyJhY3Rpb25fcmVmIjpudWxsLCJiaW5kaW5nIjoibG9jYWwiLCJjb"
        "3N0Ijp7InJhaWxfcmVmIjpudWxsLCJ0b2tlbnMiOiIwIiwidXNkX21pY3JvcyI6IjAiLCJ3Y"
        "WxsX21zIjoiMCJ9LCJleHQiOnsiZGV2LmhhbmRzYWwvYWN0Ijp7ImJvZHkiOnt9LCJraWQiO"
        "iJOQWp0Zks4dVB4OW1UWTNjV3VGMXlESUo0RFVoaVhEeVM4LV83aFlOcmFZIiwib3JnIjoid"
        "UVpQVJEVmZfSkR5LTByT2pTTTFsRUg2WU1TRWREMlJRaXV2aUwtQXNTVkxxNEEifX0sImluc"
        "HV0c19oYXNoIjoidUVpQVJEVmZfSkR5LTByT2pTTTFsRUg2WU1TRWREMlJRaXV2aUwtQXNTV"
        "kxxNEEiLCJsb2dfcG9saWN5IjoiYmVzdF9lZmZvcnQiLCJtZXRob2QiOiJsb2NhbDpoYW5kc"
        "2FsLmdlbmVzaXMuc2lnbiIsIm5vbmNlIjoidUVpRFZyS1dIU0ZCQ05ic2ZRQ0g0VWxfdHZLd"
        "GtraHdZclhFUEhjelQ5UktrTkEiLCJvdXRwdXRzX2hhc2giOiJ1RWlEanNNUkNtUHdjRkpyN"
        "zlNaVpiN2trSjY1QjVHU2JrMHlrbFprYmVGSzRWUSIsInBhcmVudF9wZXJmb3JtZXJfaWQiO"
        "m51bGwsInBhcmVudF9yZWNlaXB0X2hhc2giOm51bGwsInBlcmZvcm1lcl9pZCI6ImNhcm9sI"
        "iwicmVhc29uIjpudWxsLCJyZXF1ZXN0ZXJfaWQiOm51bGwsInN0YXR1cyI6Ik9LIiwidHMiO"
        "iIyMDI2LTExLTA4VDEwOjAwOjAwWiIsInYiOiJ2aXRuaS8wLjIifQ.ncCeeW9yQtMXEYSs25"
        "AuNatUR4aqXX_CwLIGuUO6xahtrbeA8tlaa-PsZnOyY59mM2XoIBA8aTaqQ9hC5-U5RA"
    ),
    # conformance/vectors/ok.approve.json, input.lineage[0].sigs[2]:
    # a local:handsal.genesis.sign act by bob, ts 2026-11-08T10:00:00Z.
    "genesis.sign.bob": (
        "eyJhbGciOiJFUzI1NiIsImtpZCI6Im9mMW5mQzlfNXdYeHRjZkRTbVRnWUZUNDlDQXhwQ21J"
        "WGMza3E2OEFYdU0ifQ.eyJhY3Rpb25fcmVmIjpudWxsLCJiaW5kaW5nIjoibG9jYWwiLCJjb"
        "3N0Ijp7InJhaWxfcmVmIjpudWxsLCJ0b2tlbnMiOiIwIiwidXNkX21pY3JvcyI6IjAiLCJ3Y"
        "WxsX21zIjoiMCJ9LCJleHQiOnsiZGV2LmhhbmRzYWwvYWN0Ijp7ImJvZHkiOnt9LCJraWQiO"
        "iJvZjFuZkM5XzV3WHh0Y2ZEU21UZ1lGVDQ5Q0F4cENtSVhjM2txNjhBWHVNIiwib3JnIjoid"
        "UVpQVJEVmZfSkR5LTByT2pTTTFsRUg2WU1TRWREMlJRaXV2aUwtQXNTVkxxNEEifX0sImluc"
        "HV0c19oYXNoIjoidUVpQVJEVmZfSkR5LTByT2pTTTFsRUg2WU1TRWREMlJRaXV2aUwtQXNTV"
        "kxxNEEiLCJsb2dfcG9saWN5IjoiYmVzdF9lZmZvcnQiLCJtZXRob2QiOiJsb2NhbDpoYW5kc"
        "2FsLmdlbmVzaXMuc2lnbiIsIm5vbmNlIjoidUVpQWJIVDFmYWFFMUlLWk1hQ0xjODh4V3VIT"
        "jBzMFJuM2dZdWFLSkxrUWN2MnciLCJvdXRwdXRzX2hhc2giOiJ1RWlEanNNUkNtUHdjRkpyN"
        "zlNaVpiN2trSjY1QjVHU2JrMHlrbFprYmVGSzRWUSIsInBhcmVudF9wZXJmb3JtZXJfaWQiO"
        "m51bGwsInBhcmVudF9yZWNlaXB0X2hhc2giOm51bGwsInBlcmZvcm1lcl9pZCI6ImJvYiIsI"
        "nJlYXNvbiI6bnVsbCwicmVxdWVzdGVyX2lkIjpudWxsLCJzdGF0dXMiOiJPSyIsInRzIjoiM"
        "jAyNi0xMS0wOFQxMDowMDowMFoiLCJ2Ijoidml0bmkvMC4yIn0.nqQVd2TrTTULwuCf5ZC7X"
        "gMba6ZttTggXiTevVylUbhET9X_KDHCK1XYXj79emo-KfDQpCUVSC9wrGZfBeWE5w"
    ),
}
