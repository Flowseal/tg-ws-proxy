package main

import (
	"crypto/rand"
	"crypto/sha1"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

const (
	pollTimeout = 25 * time.Second
	dialTimeout = 10 * time.Second
	downBuf     = 256
	readBuf     = 256 * 1024
	wsMagic     = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
)

var token = os.Getenv("RELAY_TOKEN")

type session struct {
	conn net.Conn
	down chan []byte
	done chan struct{}
	once sync.Once
}

func (s *session) close() {
	s.once.Do(func() {
		close(s.done)
		_ = s.conn.Close()
	})
}

var (
	mu       sync.Mutex
	sessions = map[string]*session{}
)

func newSID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

func normDst(d string) string {
	if d == "" {
		return ""
	}
	if strings.Contains(d, ":") {
		return d
	}
	return d + ":443"
}

func checkToken(r *http.Request) bool {
	if token == "" {
		return true
	}
	return r.URL.Query().Get("tok") == token
}

func readerLoop(s *session, sid string) {
	defer func() {
		s.close()
		mu.Lock()
		delete(sessions, sid)
		mu.Unlock()
		log.Printf("sid=%s reader exited, session removed", sid)
	}()
	buf := make([]byte, readBuf)
	for {
		n, err := s.conn.Read(buf)
		if n > 0 {
			chunk := make([]byte, n)
			copy(chunk, buf[:n])
			select {
			case s.down <- chunk:
			case <-s.done:
				log.Printf("sid=%s reader: done closed (client /close)", sid)
				return
			default:
				log.Printf("sid=%s down overflow, closing", sid)
				return
			}
		}
		if err != nil {
			log.Printf("sid=%s reader: conn read err: %v", sid, err)
			return
		}
	}
}

func handleOpen(w http.ResponseWriter, r *http.Request) {
	if !checkToken(r) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	dst := normDst(r.URL.Query().Get("dst"))
	if dst == "" {
		http.Error(w, "dst required", http.StatusBadRequest)
		return
	}
	conn, err := net.DialTimeout("tcp", dst, dialTimeout)
	if err != nil {
		w.WriteHeader(http.StatusBadGateway)
		_ = json.NewEncoder(w).Encode(map[string]any{"ok": false, "err": err.Error()})
		return
	}
	s := &session{conn: conn, down: make(chan []byte, downBuf), done: make(chan struct{})}
	sid := newSID()
	mu.Lock()
	sessions[sid] = s
	mu.Unlock()
	go readerLoop(s, sid)
	log.Printf("sid=%s open dst=%s", sid, dst)
	_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "sid": sid})
}

func handleUp(w http.ResponseWriter, r *http.Request) {
	if !checkToken(r) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	mu.Lock()
	s := sessions[r.URL.Query().Get("sid")]
	mu.Unlock()
	if s == nil {
		http.Error(w, "no session", http.StatusGone)
		return
	}
	if _, err := io.Copy(s.conn, r.Body); err != nil {
		log.Printf("sid up write err: %v", err)
		s.close()
		http.Error(w, "write failed", http.StatusBadGateway)
		return
	}
	w.WriteHeader(http.StatusOK)
}

func handleDown(w http.ResponseWriter, r *http.Request) {
	if !checkToken(r) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	mu.Lock()
	s := sessions[r.URL.Query().Get("sid")]
	mu.Unlock()
	if s == nil {
		w.WriteHeader(http.StatusGone)
		return
	}
	select {
	case chunk, ok := <-s.down:
		if !ok {
			w.WriteHeader(http.StatusGone)
			return
		}
		w.Header().Set("Content-Type", "application/octet-stream")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write(chunk)
	case <-time.After(pollTimeout):
		w.WriteHeader(http.StatusNoContent)
	case <-r.Context().Done():
	}
}

func handleClose(w http.ResponseWriter, r *http.Request) {
	if !checkToken(r) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	mu.Lock()
	s := sessions[r.URL.Query().Get("sid")]
	delete(sessions, r.URL.Query().Get("sid"))
	mu.Unlock()
	if s != nil {
		s.close()
	}
	w.WriteHeader(http.StatusOK)
}

func handleProbe(w http.ResponseWriter, r *http.Request) {
	if !checkToken(r) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	dst := normDst(r.URL.Query().Get("dst"))
	conn, err := net.DialTimeout("tcp", dst, dialTimeout)
	if err != nil {
		_ = json.NewEncoder(w).Encode(map[string]any{"ok": false, "err": err.Error()})
		return
	}
	_ = conn.Close()
	_ = json.NewEncoder(w).Encode(map[string]any{"ok": true})
}

// handleWsEcho — минимальный WS-эхо (stdlib only) для проверки,
// пропускает ли корп-прокси Upgrade: websocket к этому домену.
func handleWsEcho(w http.ResponseWriter, r *http.Request) {
	if !checkToken(r) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	if r.Header.Get("Upgrade") != "websocket" {
		http.Error(w, "expected websocket upgrade", http.StatusBadRequest)
		return
	}
	key := r.Header.Get("Sec-WebSocket-Key")
	if key == "" {
		http.Error(w, "missing Sec-WebSocket-Key", http.StatusBadRequest)
		return
	}
	h := sha1.New()
	h.Write([]byte(key + wsMagic))
	accept := base64.StdEncoding.EncodeToString(h.Sum(nil))
	hj, ok := w.(http.Hijacker)
	if !ok {
		http.Error(w, "hijack not supported", http.StatusInternalServerError)
		return
	}
	conn, bufrw, err := hj.Hijack()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	defer conn.Close()
	_, _ = bufrw.WriteString("HTTP/1.1 101 Switching Protocols\r\n" +
		"Upgrade: websocket\r\nConnection: Upgrade\r\n" +
		"Sec-WebSocket-Accept: " + accept + "\r\n\r\n")
	_ = bufrw.Flush()
	log.Printf("ws echo: handshake ok, remote=%s", conn.RemoteAddr())
	buf := make([]byte, 65536)
	for {
		n, err := conn.Read(buf)
		if err != nil {
			log.Printf("ws echo: read err: %v", err)
			return
		}
		if n < 2 {
			continue
		}
		opcode := buf[0] & 0x0F
		masked := (buf[1] & 0x80) != 0
		length := int(buf[1] & 0x7F)
		offset := 2
		if length == 126 {
			if n < 4 {
				continue
			}
			length = int(buf[2])<<8 | int(buf[3])
			offset = 4
		} else if length == 127 {
			if n < 10 {
				continue
			}
			length = 0
			for i := 0; i < 8; i++ {
				length = length<<8 | int(buf[2+i])
			}
			offset = 10
		}
		var maskKey []byte
		if masked {
			if n < offset+4 {
				continue
			}
			maskKey = buf[offset : offset+4]
			offset += 4
		}
		if n < offset+length {
			continue
		}
		payload := make([]byte, length)
		copy(payload, buf[offset:offset+length])
		if masked {
			for i := range payload {
				payload[i] ^= maskKey[i%4]
			}
		}
		if opcode == 0x8 {
			log.Printf("ws echo: close frame")
			return
		}
		resp := []byte{0x80 | opcode}
		if length < 126 {
			resp = append(resp, byte(length))
		} else if length < 65536 {
			resp = append(resp, 126, byte(length>>8), byte(length))
		} else {
			resp = append(resp, 127)
			for i := 7; i >= 0; i-- {
				resp = append(resp, byte(length>>(i*8)))
			}
		}
		resp = append(resp, payload...)
		if _, err = conn.Write(resp); err != nil {
			log.Printf("ws echo: write err: %v", err)
			return
		}
		log.Printf("ws echo: echoed %d bytes (opcode=0x%X)", length, opcode)
	}
}

func main() {
	if token == "" {
		log.Println("WARNING: RELAY_TOKEN not set — running in OPEN mode (unsafe for public host)")
	}
	port := os.Getenv("PORT")
	if port == "" {
		port = "80"
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/open", handleOpen)
	mux.HandleFunc("/up", handleUp)
	mux.HandleFunc("/down", handleDown)
	mux.HandleFunc("/close", handleClose)
	mux.HandleFunc("/probe", handleProbe)
	mux.HandleFunc("/wsecho", handleWsEcho)
	log.Printf("relay listening on :%s", port)
	log.Fatal(http.ListenAndServe(":"+port, mux))
}
