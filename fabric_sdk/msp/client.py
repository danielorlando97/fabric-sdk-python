from typing import Tuple
from fabric_sdk.context import ContextClient
from fabric_sdk.common import HttpClient, HttpProtocol, Ecies, Crypto
from fabric_sdk.common.crypto_tools import decode_csr
import base64
import json

from fabric_sdk.domain.network_members import EnrolledMember, UnenrolledMember, UnregisteredMember


class CAClient:
    def __init__(
        self,
        context: ContextClient,
        ca_name: str = None,
        http_client: HttpProtocol = HttpClient,
        crypto_algorithm: Crypto = None
    ) -> None:
        """Init new ca's client by context and maybe a ca's name

        :param context: context with network config
        :type context: ContextClient

        :param ca_name: name of specific ca. Context can has
                        more that one ca
        :type ca_name: str

        :param http_client: Http client to communicate with server
        :type http_client: HttpProtocol
        """

        self.http_client = http_client
        self._crypto_primitives = Ecies() if crypto_algorithm is None else crypto_algorithm

        try:
            if ca_name is None:
                self.__ca_config = context.ca_list[0]
            else:
                self.__ca_config = [
                    ca for ca in context.ca_list if ca.name == ca_name][0].url
        except IndexError:
            raise Exception()

    def __path(self, path):
        return self.__ca_config.url + path

    def generate_auth_token(self, req, cert, private_key):
        """Generate authorization token required for accessing fabric-ca APIs

        :param req: request body
        :type req: dict
        :param registrar: Required. The identity of the registrar
        (i.e. who is performing the request)
        :type registrar: Enrollment
        :return: auth token
        """
        b64Cert = base64.b64encode(cert)

        if req:
            reqJson = json.dumps(req, ensure_ascii=False)
            b64Body = base64.b64encode(reqJson.encode())

            # /!\ cannot mix f format and b
            # https://stackoverflow.com/questions/45360480/is-there-a-
            # formatted-byte-string-literal-in-python-3-6
            bodyAndCert = b'%s.%s' % (b64Body, b64Cert)
        else:
            bodyAndCert = b'.%s' % b64Cert

        sig = self._cryptoPrimitives.sign(private_key, bodyAndCert)
        b64Sign = base64.b64encode(sig)

        # /!\ cannot mix f format and b
        return b'%s.%s' % (b64Cert, b64Sign)

    def enroll(
        self,
        network_member: UnenrolledMember,
        profile: str = '',
        attr_reqs: list = None
    ) -> EnrolledMember:
        """Enroll a registered user in order to receive a signed X509
         certificate

        :param network_member: The network's member registered in CA, buy not enroll yet
        :type network_member: WithoutEnrollMember

        :param profile: The profile name.  Specify the 'tls' profile for a
             TLS certificate; otherwise, an enrollment certificate is issued. (Default value = '')
        :type profile: str

        :param attr_reqs: An array of AttributeRequest
        :type attr_reqs: list

        :return: EnrollmentMember
        :raises RequestException: errors in requests.exceptions
        :raises ValueError: Failed response, json parse error, args missing
        """

        if attr_reqs:
            if not isinstance(attr_reqs, list):
                raise ValueError(
                    "attr_reqs must be an array of AttributeRequest objects")
            for attr in attr_reqs:
                if not attr['name']:
                    raise ValueError(
                        "attr_reqs object is missing the name of the attribute")

        private_key = None
        if not csr:
            private_key = self._crypto_primitives.generate_private_key()
            csr = self._crypto_primitives.generate_csr(
                private_key, network_member.enrollment_id)
            csr = decode_csr(csr)

        req = HttpProtocol.build_http_data({
            'certificate_request':  csr,
            'caname': self.__ca_config.name,
            'profile': profile,
            'attr_reqs': attr_reqs
        })

        res, st = self.http_client.post(
            path=self.__path('enroll'),
            json=req,
            auth=(network_member.enrollment_id,
                  network_member.enrollment_secret),
            ** self.__ca_config.http_options
        )

        if res['success']:
            return network_member.enroll(
                base64.b64decode(res['result']['Cert']),
                base64.b64decode(res['result']['ServerInfo']['CAChain']),
                private_key
            )

        else:
            raise ValueError("Enrollment failed with errors {0}"
                             .format(res['errors']))

    def register(
        self,
        outsider_member: UnregisteredMember,
        network_member: EnrolledMember,
        maxEnrollments: int,
        attrs: dict,
    ) -> UnenrolledMember:
        """Register a user in order to receive a secret.
           Transform a UnregisteredMember to UnenrolledMember

        :param outsider_member: Member that will attempt to join the network
        :type outsider_member: UnregisteredMember

        :param network_member: CA-certified member of the network
        :type network_member: EnrolledMember

        :param maxEnrollments: The maximum number of times the user is
                               permitted to enroll
        :type maxEnrollments: int

        :param attrs: Array of key/value attributes to assign to the user
        :type attrs: dict

        :return UnenrolledMember with secret to use when this member enrolls
        :raises RequestException: errors in requests.exceptions
        :raises ValueError: Failed response, json parse error, args missing
        """

        req = HttpProtocol.build_http_data({
            "id": outsider_member.enrollmentID,
            "affiliation": outsider_member.affiliation,
            "max_enrollments": maxEnrollments,
            "type": outsider_member.role,
            "attrs": attrs,
            "secret": outsider_member.enrollment_secret
        })

        authorization = self.generateAuthToken(
            req, network_member.enrollment_cert, network_member.private_key)

        res, st = self.http_client.post(
            path=self.__path("register"),
            json=req,
            headers={
                'Authorization': authorization},
            ** self.__ca_config.http_options)

        if res['success']:
            return outsider_member.registry(res['result']['secret'])
        else:
            raise ValueError("Registering failed with errors {0}"
                             .format(res['errors']))
