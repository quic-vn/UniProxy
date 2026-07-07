# UniProxy - Learning-based

This repository is a research fork and extended implementation based on the original [UniProxy repository](https://github.com/Liu-Jinhao/UniProxy).

The original UniProxy was developed for the paper:

> **UniProxy: Breaking the Per-Flow Barrier in Multipath Proxy Design**
> Published in PACMNET (CoNEXT'26).
> [Paper](https://dl.acm.org/doi/abs/10.1145/3786293)

We gratefully acknowledge the original authors for releasing the UniProxy source code.

This repository preserves the core UniProxy architecture and implementation while serving as a research platform for further investigation and development of multipath proxy and MPQUIC scheduling mechanisms. Modifications and extensions developed in this repository are maintained by the **QUIC-VN research group**.

## How to Use UniProxy

Run `go build` in `./quic-tun/client/cmd` and `./quic-tun/server/cmd` to compile the client- and server-side proxy programs, respectively.

At the server-side proxy, run:

```bash
./quic-tun/server/cmd/cmd --listen-on 10.0.6.2:7500
```

The address of the server-side proxy is `10.0.6.2:7500`, you can change it to the actual address in use.

At the client-side proxy, run `client.sh`, which includes:

```bash
./quic-tun/client/cmd/cmd --listen-on tcp:127.0.0.1:6500 --server-endpoint 10.0.6.2:7500
ip rule add fwmark 1 table 100
ip route add local 0.0.0.0/0 dev lo table 100
iptables -t mangle -A PREROUTING --in-interface r1-eth0 -p tcp -d 10.0.7.0/24 -j TPROXY --on-port 6500 --on-ip 127.0.0.1 --tproxy-mark 1/1
```

The client-side proxy is listening on `127.0.0.1:6500`. The `iptables` command and the corresponding routing commands intercept TCP packets that enter the through `r1-eth0` interface and have a destination IP within the `10.0.7.0/24` subnet, and redirect them to the client-side proxy's listening port.  The `iptables` command can be modified according to actual circumstances or specific needs. 

To change the scheduling algorithm, modify:

- *SCHE_ALGO* in `./mp-quic/scheduler.go`

To change the receive window size, modify:

- *InitialConnectionFlowControlWindow* in `./mp-quic/internal/protocol/protocol.go`
- *ReceiveConnectionFlowControlWindow* in `./mp-quic/internal/protocol/server_parameters.go`
- *DefaultMaxReceiveConnectionFlowControlWindowServer* in `./mp-quic/internal/protocol/server_parameters.go`
- *DefaultMaxReceiveConnectionFlowControlWindowClient* in `./mp-quic/internal/protocol/server_parameters.go`

After modifying these parameters, recompile the proxy programs for the changes to take effect.

Additionally, we provide example programs in `example/` as references for creating the topology in Mininet and generating client-to-server download requests. 

## Key Implementation in UniProxy
To modify the original proxy into a transparent proxy, we modify the functions *Start* at line 27 and *handshake* at line 121 in `./quic-tun/client.go`.

We implement the function *SetTCPConn* at line 286 in `./mp-quic/stream.go` to record the corresponding TCP socket in the stream. The client- and server-side proxies call the function when proxying a new flow and creating the MPQUIC stream. This helps us to get the remaining data in the corresponding TCP socket for a given stream in stream scheduling.

We implement the function *streamAllocation* at line 539 in `./mp-quic/scheduler.go` to assign a stream to the currently fastest path. The function is called in the function *putStream* at line 435 in `./mp-quic/streams_map.go` to assign each new stream when created. We implement the function *adjustAllocation* at line 559 in `./mp-quic/scheduler.go` to adjust the stream-to-path assignment, which is periodically called in *sendPacket* at line 697 in `./mp-quic/scheduler.go` through a timer.

We implement the function *TokenRoundRobinIterate* at line 277 in `./mp-quic/scheduler.go` to perform TRR scheduling. It calls the function *getStreamForPath* to get the stream for sending data over the currently scheduled path. We also implement the function *distributeToken* to distribute tokens to streams in a Round-Robin manner.
