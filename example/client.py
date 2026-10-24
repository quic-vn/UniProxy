import socket
import random
import time
from threading import Thread
import csv

SERVER_HOST = '10.0.7.2'
SERVER_PORT = 5000
BUFFER_SIZE = 4096

RATE_MAX = 150000000
INTERVAL = 10

random.seed(123)

def request_fixed_size(size):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        start_time = time.time()
        client.connect((SERVER_HOST, SERVER_PORT))
        client.sendall(f"size {size}\n".encode())
        received_bytes = 0
        while received_bytes < size:
            data = client.recv(BUFFER_SIZE)
            received_bytes += len(data)
        elapsed = time.time() - start_time
        return elapsed

def request_fixed_rate(rate, interval):
    global throughput
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.connect((SERVER_HOST, SERVER_PORT))
        client.sendall(f"rate {rate}\n".encode())

        received_bytes = 0
        start_time = time.time()
        while time.time() - start_time < interval:
            data = client.recv(BUFFER_SIZE)
            if not data:
                break
            received_bytes += len(data)

            elapsed = time.time() - start_time
            speed = received_bytes / elapsed if elapsed > 0 else 0

        speed = received_bytes * 8 / (elapsed * 1000000) if elapsed > 0 else 0
        throughput += speed



if __name__ == "__main__":
    num = 5
    # num = 10  #(Number of threads to run concurrently)
    throughput = 0
    request_fixed_rate(RATE_MAX, INTERVAL)
    time.sleep(3)
    throughputs = []   
    for index in range(10):
        print(f"Test {index+1}")
        threads = []
        throughput = 0
        for i in range(num):
            threads.append(Thread(target=request_fixed_rate, args=(150000000, 10)))
        for i in range(num):
            threads[i].start()
        for i in range(num):
            threads[i].join()
        throughputs.append(throughput)
        print(throughput)
        time.sleep(3)
    with open("./throughput.csv" , 'w', newline='') as f:
        writer = csv.writer(f)
        for throughput in throughputs:
            writer.writerow([throughput])