import QtQuick 2.15

Column {
    id: root
    spacing: 12
    property var selectedDevice: null

    Text {
        width: parent.width
        text: "Scan for nearby Bluetooth devices and connect."
        color: theme.text_muted
        font.family: theme.font_family
        font.pixelSize: 13
        wrapMode: Text.WordWrap
    }

    Rectangle {
        width: parent.width
        height: Math.min(220, Math.max(60, listView.contentHeight))
        radius: 8
        color: theme.surface_alt
        clip: true

        ListView {
            id: listView
            anchors.fill: parent
            model: backend.panelData.devices || []
            delegate: Rectangle {
                width: listView.width
                height: 44
                color: (root.selectedDevice && root.selectedDevice.mac === modelData.mac) ? theme.accent : "transparent"
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: 10
                    anchors.right: parent.right
                    anchors.rightMargin: 10
                    elide: Text.ElideRight
                    text: modelData.label || modelData.name
                    color: theme.text
                    font.family: theme.font_family
                    font.pixelSize: 13
                }
                MouseArea { anchors.fill: parent; onClicked: root.selectedDevice = modelData }
            }
        }
    }

    Text {
        visible: (backend.panelData.devices || []).length === 0
        text: backend.panelBusy ? "Scanning..." : "No devices yet. Tap Refresh."
        color: theme.text_muted
        font.family: theme.font_family
        font.pixelSize: 13
    }

    Row {
        spacing: 10

        Rectangle {
            width: 110
            height: 36
            radius: 8
            color: theme.surface_alt
            opacity: backend.panelBusy ? 0.5 : 1
            Text { anchors.centerIn: parent; text: "Refresh"; color: theme.text; font.family: theme.font_family; font.pixelSize: 13 }
            MouseArea { anchors.fill: parent; enabled: !backend.panelBusy; onClicked: backend.panelAction("refresh", {}) }
        }
        Rectangle {
            width: 110
            height: 36
            radius: 8
            color: theme.surface_alt
            opacity: (!backend.panelBusy && root.selectedDevice) ? 1 : 0.5
            Text { anchors.centerIn: parent; text: "Remove"; color: theme.text; font.family: theme.font_family; font.pixelSize: 13 }
            MouseArea {
                anchors.fill: parent
                enabled: !backend.panelBusy && root.selectedDevice !== null
                onClicked: backend.panelAction("remove", { mac: root.selectedDevice.mac })
            }
        }
        Rectangle {
            width: 110
            height: 36
            radius: 8
            color: theme.accent
            opacity: (!backend.panelBusy && root.selectedDevice) ? 1 : 0.5
            Text { anchors.centerIn: parent; text: "Connect"; color: "white"; font.family: theme.font_family; font.pixelSize: 13; font.bold: true }
            MouseArea {
                anchors.fill: parent
                enabled: !backend.panelBusy && root.selectedDevice !== null
                onClicked: backend.panelAction("connect", { mac: root.selectedDevice.mac })
            }
        }
    }
}
