/*
 * This file is open source software, licensed to you under the terms
 * of the Apache License, Version 2.0 (the "License").  See the NOTICE file
 * distributed with this work for additional information regarding copyright
 * ownership.  You may not use this file except in compliance with the License.
 *
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */

#include "core/app-template.hh"
#include "core/distributed.hh"

using namespace seastar;
using namespace net;

namespace hero {

class server {
private:
    uint16_t _port;
    lw_shared_ptr<server_socket> _listener;

    struct connection {
        connected_socket _socket;
        socket_address _addr;
        input_stream<char> _in;
        output_stream<char> _out;

        connection(connected_socket&& socket, socket_address addr)
            : _socket(std::move(socket))
            , _addr(addr)
            , _in(_socket.input())
            , _out(_socket.output())
        {
        }

        ~connection() {
        }

        future<> process() {
            return _in.read().then([this] (auto&& data) mutable {
                if (!data.empty()) {
                    return _out.write(std::move(data)).then([this] {
                        return _out.flush();
                    });
                } else {
                    return _in.close();
                }
             });
        }

    };

public:
    server(uint16_t port = 1883) : _port(port) {}

    void start() {
        listen_options lo;
        lo.reuse_address = true;
        _listener = engine().listen(make_ipv4_address({_port}), lo);
        keep_doing([this] {
            return _listener->accept().then([this] (connected_socket fd, socket_address addr) mutable {
                auto conn = make_lw_shared<connection>(std::move(fd), addr);
                do_until([conn] { return conn->_in.eof(); }, [conn] {
                    return conn->process().then([conn] {
                        return conn->_out.flush();
                    });
                }).finally([conn] {
                    return conn->_out.close().finally([conn]{});
                });
            });
        }).or_terminate();
    }

    future<> stop() { return make_ready_future<>(); }
};

} /* namespace hero */

using namespace hero;

int main(int argc, char **argv) {
    distributed<server> shard_echo_server;

    namespace bpo = boost::program_options;
    app_template app;
    app.add_options()
        ("port", bpo::value<uint16_t>()->default_value(1883), "The TCP port which the echo server will listen on");

    return app.run_deprecated(argc, argv, [&] {
        engine().at_exit([&] { return shard_echo_server.stop(); });

        auto&& config = app.configuration();
        uint16_t port = config["port"].as<uint16_t>();
        return shard_echo_server.start(port).then([&] {
            return shard_echo_server.invoke_on_all(&server::start);
        }).then([&, port] {
            std::cout << "TCP echo server listen on: " << port << "\n";
        });
    });
}
