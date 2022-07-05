from __future__ import annotations

from datetime import datetime, timezone
from typing import BinaryIO, Optional

from serialization_tools.structx import Struct

from relic.sga import _abc, _serializers as _s
from relic.sga._abc import ArchivePtrs
from relic.sga._serializers import read_toc, load_lazy_data
from relic.sga.errors import VersionMismatchError
from relic.errors import MismatchError
from relic.sga.protocols import StreamSerializer
from relic.sga._core import StorageType, VerificationType, MagicWord, Version
from relic.sga.v7 import core

folder_layout = Struct("<I 4I")
folder_serializer = _s.FolderDefSerializer(folder_layout)

drive_layout = Struct("<64s 64s 5I")
drive_serializer = _s.DriveDefSerializer(drive_layout)

file_layout = Struct("<5I 2B 2I")


class FileDefSerializer(StreamSerializer[core.FileDef]):
    def __init__(self, layout: Struct):
        self.layout = layout

    def unpack(self, stream: BinaryIO) -> core.FileDef:
        name_rel_pos, data_rel_pos, length, store_length, modified_seconds, verification_type_val, storage_type_val, crc, hash_pos = self.layout.unpack_stream(stream)

        modified = datetime.fromtimestamp(modified_seconds, timezone.utc)
        storage_type: StorageType = StorageType(storage_type_val)
        verification_type: VerificationType = VerificationType(verification_type_val)

        return core.FileDef(name_rel_pos, data_rel_pos, length, store_length, storage_type, modified, verification_type, crc,hash_pos)

    def pack(self, stream: BinaryIO, value: core.FileDef) -> int:
        modified: int = int(value.modified.timestamp())
        storage_type = value.storage_type.value  # convert enum to value
        verification_type = value.verification.value  # convert enum to value
        args = value.name_pos, value.data_pos, value.length_on_disk, value.length_in_archive, modified, verification_type, storage_type, value.crc, value.hash_pos
        packed:int = self.layout.pack_stream(stream, *args)
        return packed


file_serializer = FileDefSerializer(file_layout)
toc_layout = Struct("<8I")
toc_header_serializer = _s.TocHeaderSerializer(toc_layout)


class APISerializers(_abc.ArchiveSerializer):
    version:Version

    def read(self, stream: BinaryIO, lazy: bool = False, decompress: bool = True) -> core.Archive:
        MagicWord.read_magic_word(stream)
        version = Version.unpack(stream)
        if version != self.version:
            raise VersionMismatchError(version,self.version)


        encoded_name: bytes
        encoded_name, header_size, data_pos, rsv_1 = self.layout.unpack_stream(stream)
        if rsv_1 != 1:
            raise MismatchError("Reserved Field", rsv_1, 1)
        header_pos = stream.tell()
        ptrs = ArchivePtrs(header_pos,header_size,data_pos)
        # stream.seek(header_pos)
        toc_header = self.TocHeader.unpack(stream)
        unk_a, block_size = self.metadata_layout.unpack_stream(stream)
        drives, files = read_toc(
            stream=stream,
            toc_header=toc_header,
            ptrs=ptrs,
            # header_pos=header_pos,
            # data_pos=data_pos,
            drive_def=self.DriveDef,
            file_def=self.FileDef,
            folder_def=self.FolderDef,
            decompress=decompress,
            build_file_meta=lambda _: None,  # V2 has no metadata
            name_toc_is_count=True
        )

        if not lazy:
            load_lazy_data(files)

        name: str = encoded_name.rstrip(b"").decode("utf-16-le")
        metadata = core.ArchiveMetadata(unk_a, block_size)

        return core.Archive(name, metadata, drives)

    def write(self, stream: BinaryIO, archive: core.Archive) -> int:
        raise NotImplementedError

    def __init__(self) -> None:
        self.DriveDef = drive_serializer
        self.FolderDef = folder_serializer
        self.FileDef = file_serializer
        self.TocHeader = toc_header_serializer
        self.version = core.version
        self.layout = Struct("<128s 3I")
        self.metadata_layout = Struct("<2I")
