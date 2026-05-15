import socket
import struct
import time

class GatewayServer:
    def __init__(self, host='0.0.0.0', port=1883):
        self.host = host
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.host, self.port))
        
        # 세션 관리 테이블
        # 형태: { 'client_id': {'status': 'active', 'last_seen': timestamp, 'addr': (ip, port)} }
        self.sessions = {}
        
        print(f"Gateway Server started on UDP {self.host}:{self.port}")

    def handle_connect(self, addr, data):
        # Header (2 bytes) + client_id (16 bytes) = 18 bytes
        if len(data) < 18:
            return
            
        header_len, msg_type = struct.unpack('<BB', data[:2])
        client_id = data[2:18].decode('utf-8', errors='ignore').rstrip('\x00')
        
        self.sessions[client_id] = {
            'status': 'active',
            'last_seen': time.time(),
            'addr': addr
        }
        print(f"[CONNECT] Session created for Client ID: {client_id} from {addr}")
        
        # TODO: [팀원 역할] 연결 성공 이력을 CSV 또는 DB에 로깅하는 로직 추가

    def handle_publish(self, addr, data):
        # Header (2 bytes) + msg_id (2 bytes) + qos (1 byte) + topic_id (2 bytes) = 7 bytes
        if len(data) < 7:
            return
            
        header_len, msg_type, msg_id, qos, topic_id = struct.unpack('<BBHBH', data[:7])
        payload = data[7:].decode('utf-8', errors='ignore').rstrip('\x00')
        
        print(f"[PUBLISH] Received from {addr} - MsgID: {msg_id}, QoS: {qos}, Topic: {topic_id}, Payload: {payload}")
        
        # TODO: [팀원 역할] BME280 데이터 파싱 후 Streamlit 대시보드 시각화를 위한 데이터 추출 및 로깅
        # 예: log_to_csv(time.time(), msg_id, qos, payload, success=True)
        
        # QoS 1 메시지 수신 시 반드시 PUBACK 전송
        if qos == 1:
            self.send_puback(addr, msg_id)

    def send_puback(self, addr, msg_id):
        # PUBACK Packet: Header (Length 4, Type 3) + MsgID (2 bytes)
        packet = struct.pack('<BBH', 4, 3, msg_id)
        self.sock.sendto(packet, addr)
        print(f"[PUBACK] Sent PUBACK for MsgID: {msg_id} to {addr}")

    def handle_disconnect(self, addr, data):
        print(f"[DISCONNECT] Received from {addr}")
        # 발신자 주소로 활성화된 세션을 찾아 수면 상태로 변경
        for client_id, session in self.sessions.items():
            if session.get('addr') == addr:
                session['status'] = 'asleep'
                print(f"[SESSION] Client '{client_id}' went asleep.")
                break
                
        # TODO: [팀원 역할] 세션 종료/수면 상태 전환 이력을 로그로 남기는 로직 추가

    def run(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(1024)
                if len(data) < 2:
                    continue
                    
                msg_type = data[1]
                
                if msg_type == 1: # CONNECT
                    self.handle_connect(addr, data)
                elif msg_type == 2: # PUBLISH
                    self.handle_publish(addr, data)
                elif msg_type == 4: # DISCONNECT
                    self.handle_disconnect(addr, data)
                else:
                    print(f"Unknown message type: {msg_type}")
                    
            except KeyboardInterrupt:
                print("\nGateway Server shutting down.")
                break
            except Exception as e:
                print(f"Error handling packet from {addr}: {e}")

if __name__ == "__main__":
    server = GatewayServer()
    server.run()
