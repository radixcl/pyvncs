from struct import *

class ClipboardController():

    def __init__(self):
        pass
    
    def client_cut_text(self, sock):
        """
        The client has new ISO 8859-1 (Latin-1) text in its cut buffer.
        Ends of lines are represented by the linefeed / newline character (value 10) alone. No carriage-return (value 13) is needed.

        No. of bytes	Type	[Value]	Description
        1	            U8      6       message-type
        3	 	 	                    padding
        4	            U32	 	        length
        length	        U8 array	 	text
        """
        
        # read padding
        _ = sock.recv(3)

        # read length
        length = sock.recv(4)
        (length, ) = unpack('!I', length)

        # read text
        text = sock.recv(length)

        return text